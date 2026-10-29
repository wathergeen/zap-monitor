from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="E-mail Institucional",
        widget=forms.EmailInput(
            attrs={
                "class": "w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:outline-none transition",
                "placeholder": "seu.email@empresa.com",
                "autocomplete": "email",
                "autofocus": "autofocus",
            }
        ),
    )
    password = forms.CharField(
        label="Senha de Acesso",
        widget=forms.PasswordInput(
            attrs={
                "class": "w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:outline-none transition",
                "placeholder": "••••••••••••",
                "autocomplete": "current-password",
            }
        ),
    )

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        password = cleaned_data.get("password")

        if email and password:
            # Mensagem de erro genérica sem enumerar e-mails existentes
            self.user = authenticate(request=self.request, username=email, password=password)
            if self.user is None or not self.user.is_active:
                raise forms.ValidationError("Credenciais inválidas. Verifique seu e-mail e senha.")
        return cleaned_data


class TOTPVerifyForm(forms.Form):
    code = forms.CharField(
        label="Código de Autenticação (6 dígitos) ou Código de Recuperação",
        max_length=32,
        widget=forms.TextInput(
            attrs={
                "class": "w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:outline-none text-center font-mono tracking-widest text-lg",
                "placeholder": "123456",
                "autocomplete": "one-time-code",
                "autofocus": "autofocus",
            }
        ),
    )


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        label="E-mail para recuperação",
        widget=forms.EmailInput(
            attrs={
                "class": "w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "placeholder": "seu.email@empresa.com",
            }
        ),
    )


class AcceptInvitationForm(forms.Form):
    full_name = forms.CharField(
        label="Nome Completo",
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "placeholder": "Seu Nome Completo",
            }
        ),
    )
    password = forms.CharField(
        label="Nova Senha (Mínimo 12 caracteres)",
        widget=forms.PasswordInput(
            attrs={
                "class": "w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "placeholder": "••••••••••••",
            }
        ),
    )
    password_confirm = forms.CharField(
        label="Confirmação da Nova Senha",
        widget=forms.PasswordInput(
            attrs={
                "class": "w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "placeholder": "••••••••••••",
            }
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get("password")
        p2 = cleaned_data.get("password_confirm")

        if p1 and p2:
            if p1 != p2:
                raise forms.ValidationError("As senhas não coincidem.")
            try:
                validate_password(p1, user=self.user)
            except forms.ValidationError as e:
                self.add_error("password", e)
        return cleaned_data
