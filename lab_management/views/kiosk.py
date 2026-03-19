import json
import base64
import requests
import urllib3
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views import View
from django.utils import timezone
from django.http import JsonResponse

# ปิดการแจ้งเตือนเรื่อง SSL เผื่อกรณี API มหาลัยใช้ Certificate ภายใน
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Models และ Forms ที่ต้องใช้
from lab_management.models import Computer, Software, SiteConfig, Booking, UsageLog
from lab_management.forms.kiosk import CheckinForm


class IndexView(View):
    def get(self, request):
        config = SiteConfig.objects.first()
        
        # ✅ เพิ่มการดักจับ: เผื่อกรณีที่ระบบยังไม่มีการตั้งค่าใดๆ เลย ให้สร้างค่าเริ่มต้นไว้
        if not config:
            config = SiteConfig.objects.create(lab_name="CKLab", is_open=True)
        
        # บังคับใช้ PC-01 เป็นค่าเริ่มต้นหากไม่ได้ระบุใน URL
        pc_name = request.GET.get('pc')
        if not pc_name:
            pc_name = 'PC-01'
            
        computer = Computer.objects.filter(name=pc_name).first()
        
        # ✅ ระบบ "กันหลุด" & "Auto-Fix"
        if computer and computer.status.upper() == 'IN_USE':
            
            # ค้นหาประวัติที่ยังไม่ได้ Check-out
            active_log = UsageLog.objects.filter(computer=computer.name, end_time__isnull=True).last()
            
            # (เขียนดักเผื่อกรณี field computer ใน UsageLog ถูกตั้งเป็น ForeignKey)
            if not active_log:
                try:
                    active_log = UsageLog.objects.filter(computer=computer, end_time__isnull=True).last()
                except:
                    pass

            if active_log:
                # 🟢 กรณีปกติ: เจอประวัติที่ยังไม่เช็คเอาท์ -> เด้งไปหน้า Timer ทันที (พร้อมเวลาเดิม)
                # ✅ แก้ไขแล้ว: ใช้ timezone.now() แทน เพื่อป้องกันบั๊ก Timezone +7 ทำให้เวลาค้าง
                start_time_ms = int(active_log.start_time.timestamp() * 1000) if active_log.start_time else int(timezone.now().timestamp() * 1000)
                sw_name = computer.Software.name if computer.Software else "General Use"
                
                context = {
                    'computer': computer,
                    'log_id': active_log.id,
                    'software_name': sw_name,
                    'start_time_ms': start_time_ms,
                    'user_name': active_log.user_name
                }
                return render(request, 'cklab/kiosk/timer.html', context)
            else:
                # 👻 GHOST STATE: สถานะเครื่องค้าง (IN_USE แต่หาข้อมูลคนนั่งไม่เจอ)
                # Auto-Fix: ปรับสถานะเครื่องกลับเป็น "ว่าง" อัตโนมัติ เพื่อให้หน้าจอไม่เป็นปุ่มเทาค้าง
                computer.status = 'AVAILABLE'
                computer.save()
        
        context = {
            'config': config,
            'computer_name': pc_name
        }
        return render(request, 'cklab/kiosk/index.html', context)

    def post(self, request):
        pass


class StatusView(View):
    def get(self, request, pc_id):
        # API สำหรับคืนค่าสถานะเครื่องให้ Frontend เช็คแบบ Real-time
        computer = Computer.objects.filter(name=pc_id).first()
        if not computer:
            return JsonResponse({'status': 'NOT_FOUND', 'is_open': False})

        config = SiteConfig.objects.first()
        
        # ค้นหาการจองคิวถัดไป
        next_booking = Booking.objects.filter(
            computer=computer,
            status='APPROVED',
            start_time__gte=timezone.now()
        ).order_by('start_time').first()

        data = {
            'pc_id': computer.name,
            'status': computer.status,
            # หากยังไม่ได้ตั้งค่า config ให้ถือว่าเปิด (True) ไว้ก่อน เพื่อกันหน้าจอล็อก
            'is_open': config.is_open if config else True,
            'next_booking_start': next_booking.start_time.isoformat() if next_booking else None,
            'next_booking_student_id': next_booking.student_id if next_booking else None,
        }
        return JsonResponse(data)


class VerifyUserAPIView(View):
    def post(self, request):
        try:
            body = json.loads(request.body)
            student_id = body.get('student_id', '').strip()

            if not student_id:
                return JsonResponse({'status': 'error', 'message': 'กรุณาระบุรหัสนักศึกษาหรือรหัสบุคลากร'}, status=400)

            # 1. เข้ารหัสรหัสเป็น Base64
            encoded_id = base64.b64encode(student_id.encode('utf-8')).decode('utf-8')

            # 2. ยิงตรงไปดึงข้อมูล (ไม่ใช้ Token)
            data_url = "https://esapi.ubu.ac.th/api/v1/student/reg-data"
            headers = {
                "Content-Type": "application/json"
            }
            data_payload = {"loginName": encoded_id}
            
            # ✅ เพิ่ม timeout เป็น 30 วินาที ป้องกัน API มหาลัยตอบกลับช้า
            data_response = requests.post(data_url, headers=headers, json=data_payload, timeout=30, verify=False)
            
            # ยอมรับทั้ง 200 (OK) และ 201 (Created) ว่าทำงานสำเร็จ
            if data_response.status_code not in [200, 201]:
                return JsonResponse({'status': 'error', 'message': 'เกิดข้อผิดพลาดในการเชื่อมต่อกับระบบฐานข้อมูลของมหาวิทยาลัย โปรดลองใหม่อีกครั้ง'}, status=500)

            result = data_response.json()

            # เช็ค statusCode ข้างใน JSON เผื่อมหาลัยส่ง 201 มา
            if result.get('statusCode') in [200, 201] and result.get('data'):
                # ดึงข้อมูลตรงๆ เพราะ API ส่งมาเป็น Object 
                user_data = result['data'] 
                
                # ประกอบชื่อไทย
                full_name = f"{user_data.get('USERPREFIXNAME', '')}{user_data.get('USERNAME', '')} {user_data.get('USERSURNAME', '')}".strip()
                
                # ดึงชั้นปีจาก API
                student_year = str(user_data.get('STUDENTYEAR', '-'))

                # ==========================================
                # ✅ ลอจิกคัดกรอง "บุคลากร/อาจารย์"
                # ==========================================
                role = 'student'
                staff_prefixes = ['อาจารย์', 'ดร.', 'ผศ.', 'รศ.', 'ศ.', 'นพ.', 'พญ.']
                
                # ถ้ารหัสไม่ได้มีแค่ตัวเลข (เช่น scwayopu) หรือมีคำนำหน้าเป็นอาจารย์ ให้ถือว่าเป็น staff
                if not student_id.isdigit() or any(prefix in full_name for prefix in staff_prefixes):
                    role = 'staff'
                    if student_year == '0': # หากเป็นบุคลากร มักไม่มีชั้นปี หรือเป็น 0
                        student_year = '-'

                return JsonResponse({
                    'status': 'success',
                    'data': {
                        'id': student_id,
                        'name': full_name,
                        'faculty': user_data.get('FACULTYNAME', 'มหาวิทยาลัยอุบลราชธานี'),
                        'role': role, # ส่งค่าที่คัดกรองแล้วกลับไป (student หรือ staff)
                        'level': user_data.get('LEVELNAME', 'บุคคลทั่วไป' if role == 'staff' else 'ปริญญาตรี'),
                        'year': student_year
                    }
                })
            else:
                return JsonResponse({'status': 'error', 'message': 'ไม่พบรหัสผู้ใช้งานนี้ในระบบ หรือท่านยังไม่ได้ลงทะเบียนในระบบของมหาวิทยาลัย'}, status=404)

        except requests.exceptions.Timeout:
            return JsonResponse({'status': 'error', 'message': 'หมดเวลาการเชื่อมต่อ (Timeout) เซิร์ฟเวอร์ของมหาวิทยาลัยตอบกลับช้า โปรดลองใหม่อีกครั้ง'}, status=504)
        except requests.exceptions.RequestException as e:
            return JsonResponse({'status': 'error', 'message': 'ไม่สามารถเชื่อมต่อกับเครือข่ายของมหาวิทยาลัยได้ โปรดตรวจสอบอินเทอร์เน็ตหรือระบบ VPN ของท่าน'}, status=503)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'ระบบขัดข้องภายใน: {str(e)}'}, status=500)


class CheckinView(View):
    def get(self, request, pc_id):
        return redirect(f"{reverse('index')}?pc={pc_id}")

    def post(self, request, pc_id):
        computer = get_object_or_404(Computer, name=pc_id)
        config = SiteConfig.objects.first()

        if (config and not config.is_open) or computer.status not in ['AVAILABLE', 'RESERVED']:
            return redirect(f"{reverse('index')}?pc={pc_id}&error=unavailable")

        # ถ้าเครื่องสถานะ RESERVED ต้องตรวจสอบว่า user_id ตรงกับคนที่จองไว้เท่านั้น
        if computer.status == 'RESERVED':
            incoming_user_id = request.POST.get('user_id', '').strip()
            active_booking = Booking.objects.filter(
                computer=computer,
                status='APPROVED',
                start_time__lte=timezone.now() + timedelta(minutes=15),
                end_time__gte=timezone.now(),
            ).order_by('start_time').first()
            if not active_booking or incoming_user_id != active_booking.student_id:
                return redirect(f"{reverse('index')}?pc={pc_id}&error=reserved")

        # เรียกใช้ CheckinForm เพื่อกรองและตรวจสอบข้อมูลที่รับมา
        form = CheckinForm(request.POST)
        if form.is_valid():
            cleaned_data = form.cleaned_data
            
            # เช็คว่าคอมเครื่องนี้มี Software ผูกอยู่ไหม
            sw_name = computer.Software.name if computer.Software else "General Use"
            
            # สร้างประวัติการใช้งานด้วยข้อมูลที่ผ่าน Form Validation แล้ว
            usage_log = UsageLog.objects.create(
                user_id=cleaned_data.get('user_id'),
                user_name=cleaned_data.get('user_name'),
                user_type=cleaned_data.get('user_type', 'student'),
                department=cleaned_data.get('department', ''),
                user_year=cleaned_data.get('user_year', ''),  
                computer=computer.name,
                Software=sw_name # บันทึกชื่อซอฟต์แวร์ลง Log ด้วย
            )

            # อัปเดตสถานะเครื่อง
            computer.status = 'IN_USE'
            computer.save()

            # ✅ แปลงเป็น Timestamp เลย ป้องกันปัญหาเวลาค้าง 00:00:00
            start_time_ms = int(timezone.now().timestamp() * 1000)

            context = {
                'computer': computer,
                'log_id': usage_log.id,
                'software_name': sw_name,
                'start_time_ms': start_time_ms,
                'user_name': usage_log.user_name
            }
            return render(request, 'cklab/kiosk/timer.html', context)
        else:
            # หากข้อมูลที่ส่งมาไม่ถูกต้อง ให้เด้งกลับไปหน้าแรก
            return redirect(f"{reverse('index')}?pc={pc_id}&error=invalid_data")


class CheckoutView(View):
    def get(self, request, pc_id):
        return redirect(f"{reverse('index')}?pc={pc_id}")

    def post(self, request, pc_id):
        computer = get_object_or_404(Computer, name=pc_id)
        usage_log = UsageLog.objects.filter(computer=computer.name, end_time__isnull=True).last()
        
        if usage_log:
            usage_log.end_time = timezone.now()
            usage_log.save()

        computer.status = 'AVAILABLE'
        computer.save()

        log_id = usage_log.id if usage_log else 0
        return redirect('feedback', pc_id=computer.name, software_id=log_id)


class FeedbackView(View):
    def get(self, request, pc_id, software_id):
        # 1. ดึงการตั้งค่าระบบมา
        config = SiteConfig.objects.first()
        
        # 2. เตรียมลิงก์ (ถ้าแอดมินไม่ได้ตั้งค่าไว้ ให้ใช้ลิงก์ Default)
        default_url = "https://docs.google.com/forms/d/e/1FAIpQLSfnaw6G3NFsuKwngOenWfQ2pU3AQDAYbJ-ON1W5TpU8xjDeKw/viewform?embedded=true"
        current_feedback_url = config.feedback_url if (config and config.feedback_url) else default_url

        context = {
            'pc_id': pc_id,
            'log_id': software_id,
            'feedback_url': current_feedback_url  # ✅ ส่งตัวแปรลิงก์ไปที่หน้า HTML
        }
        return render(request, 'cklab/kiosk/feedback.html', context)