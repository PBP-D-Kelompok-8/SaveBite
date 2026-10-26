# Katalog Resep Bahan Sisa

Katalog adalah aplikasi Django di SaveBite. Pengguna dapat mencari resep berdasarkan nama atau bahan yang tersedia, melihat detail resep, dan menyimpan resep ke daftar pribadi.

## Fitur

- Mencari resep berdasarkan nama dan maksimal tiga bahan.
- Mengutamakan resep yang cocok dengan lebih banyak bahan.
- Melihat gambar, kategori, asal, bahan, takaran, dan cara memasak.
- Menyimpan resep, menulis catatan pribadi, mengedit catatan, dan menghapus resep.
- Menjaga daftar resep agar hanya dapat dilihat dan diubah oleh pemiliknya.
- Menampilkan pesan jika resep tidak ditemukan atau TheMealDB tidak dapat dihubungi.

## Cara kerja

1. Pengguna mengisi form pencarian.
2. JavaScript memakai Fetch API untuk meminta hasil ke Django tanpa memuat ulang halaman.
3. Jika JavaScript tidak aktif, form tetap dapat mengirim pencarian seperti form GET biasa.
4. Django memeriksa isian, lalu `services.py` mengambil data dari TheMealDB.
5. Django menampilkan hasil pada template katalog.
6. Simpan, edit, dan hapus memakai form POST Django dengan token CSRF.

Pencarian beberapa bahan menggabungkan hasil dari TheMealDB dan mengurutkannya berdasarkan jumlah bahan yang cocok. Hasilnya tidak selalu menggunakan semua bahan yang dimasukkan.

## Struktur file

- `models.py`: menyimpan resep yang disimpan pengguna.
- `forms.py`: memeriksa isian pencarian dan catatan.
- `services.py`: mengambil data dari TheMealDB.
- `views.py`: menangani halaman dan aksi katalog.
- `urls.py`: alamat halaman dan pencarian Fetch API.
- `templates/katalog/`: halaman pencarian, hasil resep, detail, daftar tersimpan, dan edit catatan.
- `static/katalog/css/`: tata letak dan tampilan katalog.
- `static/katalog/js/katalog.js`: Fetch API sederhana untuk pencarian.
- `migrations/`: perubahan struktur database.
- `tests.py`: pengujian fitur utama.

Tampilan menggunakan warna, font, tombol, input, serta nilai radius dari Design System SaveBite. Ukuran kartu dibuat lebih ringkas agar beberapa resep dapat ditampilkan dengan rapi. Gaya khusus katalog menggunakan awalan `kat-` agar tidak mengubah tampilan aplikasi lain.

## URL

| URL | Kegunaan |
|---|---|
| `/katalog/` | Mencari resep |
| `/katalog/api/search/` | Mengambil potongan HTML hasil pencarian untuk Fetch API |
| `/katalog/meal/<id>/` | Melihat detail resep |
| `/katalog/saved/` | Melihat resep tersimpan |
| `/katalog/saved/add/` | Menyimpan resep dari form detail |
| `/katalog/saved/<id>/edit/` | Mengubah catatan resep |
| `/katalog/saved/<id>/delete/` | Menghapus resep tersimpan |

## Integrasi ke proyek utama

1. Tambahkan `'katalog.apps.KatalogConfig'` ke `INSTALLED_APPS` di `SaveBite/settings.py`.
2. Pastikan `include` diimpor pada `SaveBite/urls.py`, lalu tambahkan `path('katalog/', include('katalog.urls'))` ke `urlpatterns`.
3. Jangan menghapus URL atau aplikasi lain yang sudah ada.
4. Modul menggunakan `request_board:login`, yaitu halaman login Django yang tersedia pada snapshot proyek ini.
5. Pastikan database lokal sudah siap, lalu jalankan migrasi.

## Menjalankan pemeriksaan

Dari folder utama proyek, jalankan:

```bash
python manage.py check
python manage.py migrate
python manage.py test katalog
```

Tes memakai data contoh sehingga tidak meminta data dari TheMealDB setiap kali dijalankan. Pencarian resep yang sesungguhnya tetap membutuhkan koneksi internet.

## Catatan

- Penyimpanan resep membutuhkan pengguna yang sudah login.
- Periksa hasil di browser desktop dan mobile setelah modul disambungkan ke proyek utama.
