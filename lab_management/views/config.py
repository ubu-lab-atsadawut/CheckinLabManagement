from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib import messages
from ..models import SiteConfig, AdminonDuty
from ..forms import SiteConfigForm, AdminUserForm, AdminUserEditForm

class AdminConfigView(LoginRequiredMixin, View):
    def get(self, request):
        config = SiteConfig.objects.first()
        if not config:
            config = SiteConfig.objects.create(lab_name="CKLab")
        
        admins = User.objects.filter(is_staff=True).order_by('-is_superuser', 'username')
        context = {'config': config, 'admins': admins}
        return render(request, 'cklab/admin/admin-config.html', context)

    def post(self, request):
        form_type = request.POST.get('form_type')
        config_instance = SiteConfig.objects.first()

        if form_type == 'general_config':
            form = SiteConfigForm(request.POST, instance=config_instance)
            if form.is_valid():
                config = form.save(commit=False)
                
                admin_name = request.POST.get('admin_on_duty_name')
                if admin_name:
                    duty_obj, _ = AdminonDuty.objects.get_or_create(id=1)
                    duty_obj.admin_on_duty = admin_name
                    duty_obj.contact_phone = request.POST.get('contact_phone', '')
                    duty_obj.contact_email = request.POST.get('contact_email', '')
                    duty_obj.save()
                    config.admin_on_duty = duty_obj
                
                if 'feedback_url' in request.POST:
                    config.feedback_url = request.POST.get('feedback_url')
                
                config.save()
                messages.success(request, 'บันทึกการตั้งค่าและสถานะห้องเรียบร้อยแล้ว')
            else:
                messages.error(request, 'เกิดข้อผิดพลาดในการบันทึกข้อมูล')

        elif form_type == 'add_admin':
            form = AdminUserForm(request.POST)
            if form.is_valid():
                user = form.save(commit=False)
                user.set_password(form.cleaned_data['password'])
                user.is_staff = True
                
                if request.POST.get('role') == 'Super Admin':
                    user.is_superuser = True
                    
                user.save()
                messages.success(request, f'เพิ่มผู้ดูแล {user.username} เรียบร้อยแล้ว')
            else:
                messages.error(request, 'ข้อมูลแอดมินไม่ถูกต้อง หรือ Username ซ้ำ')

        return redirect('admin_config')

class AdminUserDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        user_to_delete = get_object_or_404(User, pk=pk)
        
        if user_to_delete == request.user:
            messages.error(request, "ไม่สามารถลบบัญชีที่กำลังใช้งานอยู่ได้")
            return redirect('admin_users')
            
        if user_to_delete.is_superuser and not request.user.is_superuser:
            messages.error(request, "คุณไม่มีสิทธิ์ลบ Super Admin")
            return redirect('admin_users')

        username = user_to_delete.username
        user_to_delete.delete()
        messages.success(request, f"ลบผู้ดูแลระบบ {username} เรียบร้อยแล้ว")
        return redirect('admin_users')

class AdminUserView(LoginRequiredMixin, View):
    def get(self, request):
        admin_users = User.objects.filter(is_staff=True).order_by('-is_active', '-is_superuser', 'username')
        
        total_users = admin_users.count()
        active_users = admin_users.filter(is_active=True).count()
        
        context = {
            'admin_users': admin_users,
            'total_users': total_users,
            'active_users': active_users,
        }
        return render(request, 'cklab/admin/admin-users.html', context)

    def post(self, request):
        form = AdminUserForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.is_staff = True
            
            if request.POST.get('role') == 'Super Admin':
                user.is_superuser = True
                
            user.save()
            messages.success(request, f'เพิ่มผู้ดูแลระบบ {user.username} สำเร็จ')
        else:
            messages.error(request, 'เกิดข้อผิดพลาดในการเพิ่มบัญชี (Username อาจจะซ้ำ)')
            
        return redirect('admin_users')

class AdminUserEditView(LoginRequiredMixin, View):
    def get(self, request, pk):
        user_to_edit = get_object_or_404(User, pk=pk)
        form = AdminUserEditForm(instance=user_to_edit)
        
        return render(request, 'cklab/admin/admin-users-edit.html', {
            'form': form, 
            'user_to_edit': user_to_edit
        })

    def post(self, request, pk):
        user_to_edit = get_object_or_404(User, pk=pk)
        
        is_active_checked = request.POST.get('is_active') == 'on'
        
        if not is_active_checked:
             if user_to_edit == request.user:
                 messages.error(request, 'คุณไม่สามารถปิดการใช้งานบัญชีของตนเองได้')
                 return redirect('admin_user_edit', pk=pk)
             if user_to_edit.is_superuser and not request.user.is_superuser:
                 messages.error(request, 'คุณไม่มีสิทธิ์ปิดการใช้งาน Super Admin')
                 return redirect('admin_user_edit', pk=pk)

        user_to_edit.username = request.POST.get('username')
        user_to_edit.email = request.POST.get('email', '')
        user_to_edit.first_name = request.POST.get('first_name', '')
        user_to_edit.last_name = request.POST.get('last_name', '')
        user_to_edit.is_active = is_active_checked
        
        # ✅ รับค่ารหัสผ่านใหม่ (ถ้าปล่อยว่างคือไม่เปลี่ยน)
        new_password = request.POST.get('password')
        if new_password:
             user_to_edit.set_password(new_password)
        
        try:
             user_to_edit.save()
             messages.success(request, f'อัปเดตข้อมูลผู้ใช้ {user_to_edit.username} เรียบร้อยแล้ว')
             return redirect('admin_users')
        except Exception:
             messages.error(request, 'เกิดข้อผิดพลาด: Username นี้มีผู้ใช้งานอื่นใช้แล้ว')
             return redirect('admin_user_edit', pk=pk)