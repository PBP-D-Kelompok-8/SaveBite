from django.contrib.auth.forms import AuthenticationForm


class SaveBiteAuthenticationForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "Nama pengguna atau kata sandi tidak benar. Periksa kembali keduanya.",
        "inactive": "Akun ini tidak aktif.",
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        for field in self.fields.values():
            field.error_messages.update({
                "required": "Kolom ini wajib diisi.",
                "invalid": "Masukkan nilai yang valid.",
                "max_length": "Gunakan paling banyak %(max)s karakter.",
                "null_characters_not_allowed": "Karakter null tidak diperbolehkan.",
            })
