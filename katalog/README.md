# Katalog Resep Bahan Sisa

Katalog adalah salah satu aplikasi Django di SaveBite. Pengguna dapat mencari resep dari TheMealDB berdasarkan nama resep atau bahan yang tersedia, melihat detail resep, dan menyimpan resep ke daftar pribadi.

## Fitur

- Mencari resep berdasarkan nama dan maksimal tiga bahan.
- Menggabungkan hasil pencarian bahan dan menampilkan resep dengan kecocokan bahan terbanyak lebih dahulu.
- Melihat gambar, kategori, asal, bahan, takaran, dan cara memasak.
- Menyimpan resep, melihat daftar resep tersimpan, mengubah catatan, dan menghapus resep.
- Membatasi daftar dan perubahan resep tersimpan hanya untuk pengguna yang berhak.
- Menampilkan status saat pencarian sedang berjalan, tidak menemukan hasil, atau mengalami masalah.

## Cara kerja singkat

1. Pengguna mengisi kolom pencarian di halaman katalog.
2. JavaScript mengirim permintaan ke Django menggunakan `fetch()`.
3. Django memeriksa input lalu meminta data resep dari TheMealDB.
4. Django mengirim hasil dalam format JSON dan JavaScript menampilkan kartu resep.
5. Resep tersimpan dan catatan pribadi dikelola oleh Django dan disimpan di database.

TheMealDB versi gratis memfilter satu bahan dalam setiap permintaan. Karena itu, pencarian beberapa bahan dilakukan dengan beberapa permintaan. Katalog menggabungkan hasilnya dan mengurutkan resep berdasarkan jumlah bahan yang cocok; hasilnya tidak selalu mengandung semua bahan yang dimasukkan.

## Struktur file

- `models.py`: data resep yang disimpan pengguna.
- `forms.py`: pemeriksaan input pencarian dan catatan.
- `services.py`: pengambilan data dari TheMealDB.
- `views.py`: halaman, pencarian JSON, dan pengelolaan resep tersimpan.
- `urls.py`: alamat halaman dan endpoint katalog.
- `templates/katalog/`: halaman pencarian, detail, daftar tersimpan, dan edit catatan.
- `static/katalog/`: CSS dan JavaScript khusus katalog.
- `migrations/`: perubahan struktur database.
- `tests.py`: pengujian fitur utama katalog.

Subfolder `templates/katalog/` digunakan agar nama template katalog tidak bertabrakan dengan template aplikasi lain. Halaman katalog memakai template dasar SaveBite dan stylesheet khususnya menggunakan awalan `kat-` untuk mengurangi benturan dengan CSS global.

## URL

| URL | Kegunaan |
|---|---|
| `/katalog/` | Mencari resep |
| `/katalog/api/search/` | Mengambil hasil pencarian dalam JSON |
| `/katalog/meal/<id>/` | Melihat detail resep |
| `/katalog/saved/` | Melihat resep tersimpan |
| `/katalog/saved/add/` | Menyimpan resep |
| `/katalog/saved/<id>/edit/` | Mengubah catatan resep |
| `/katalog/saved/<id>/delete/` | Menghapus resep tersimpan |

## Integrasi ke proyek

Tambahkan `'katalog.apps.KatalogConfig'` ke `INSTALLED_APPS` di `SaveBite/settings.py`. Tambahkan `path('katalog/', include('katalog.urls'))` ke URL utama, dengan memastikan `include` diimpor. Jangan menghapus URL atau aplikasi lain yang sudah ada di proyek.

Halaman katalog menggunakan URL bernama `login` untuk mengarahkan pengunjung yang belum login. Pastikan URL login proyek sudah tersedia sebelum menguji fitur simpan resep.

## Menjalankan pemeriksaan

Dari folder utama proyek dan setelah aplikasi didaftarkan, jalankan:

```bash
python manage.py check
python manage.py test katalog
```

Sebelum menjalankan migrasi, pastikan konfigurasi menggunakan database lokal, bukan database produksi. Pengujian memakai data contoh agar tidak perlu mengandalkan API TheMealDB saat setiap tes berjalan.

## Catatan

- Pencarian resep memerlukan koneksi internet ke TheMealDB.
- Penyimpanan resep memerlukan login yang berfungsi di proyek utama.
- Tampilan akhir perlu diperiksa di browser desktop dan mobile serta diselaraskan dengan wireframe Figma kelompok.
