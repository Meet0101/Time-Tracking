from django import forms
from .models import User, Project, Module, Task, TimeLog


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'description', 'start_date', 'end_date']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }

class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['module', 'assigned_to', 'title', 'description', 'status', 'priority', 'deadline']
        widgets = {
            'deadline': forms.DateInput(attrs={'type': 'date'}),
        }

class ModuleForm(forms.ModelForm):
    class Meta:
        model = Module
        fields = ['project', 'name']
        widgets = {}

class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)

class SignupForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)
    role = forms.ChoiceField(
        choices=[("developer", "Developer"), ("manager", "Manager")],
        initial="developer",
    )
    
    class Meta:
        model = User
        fields = ['email', 'role']
    
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")
        if password != confirm_password:
            raise forms.ValidationError("Passwords do not match!")
        return cleaned_data


class OTPVerifyForm(forms.Form):
    otp = forms.CharField(
        label="Enter OTP",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={"placeholder": "6-digit OTP", "inputmode": "numeric"}),
    )


class ManualTimeEntryForm(forms.Form):
    """
    Manual time entry ke liye simple form (AJAX timer nahi).
    """

    task = forms.ModelChoiceField(queryset=Task.objects.none(), required=False)
    custom_task_title = forms.CharField(
        required=False,
        max_length=200,
        label="Or type task name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Client call, Bug fix, Design review"}),
    )
    start_time = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'})
    )
    end_time = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'})
    )

    def __init__(self, *args, tasks_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tasks_qs is not None:
            self.fields['task'].queryset = tasks_qs

    def clean(self):
        cleaned_data = super().clean()
        task = cleaned_data.get("task")
        custom_task_title = (cleaned_data.get("custom_task_title") or "").strip()
        if not task and not custom_task_title:
            raise forms.ValidationError("Select a task or type a task name.")
        return cleaned_data
