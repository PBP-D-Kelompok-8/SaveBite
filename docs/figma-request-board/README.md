# Request Board — implementasi Figma

Sumber: [Design System, halaman Request Board (347:860)](https://www.figma.com/design/Qz00q3qJVwqCvgUgkwAJ8A/Design-System?node-id=347-860).

Implementasi menggunakan template Django (HTML), CSS biasa, serta JavaScript untuk peningkatan interaksi. Halaman tersedia di `/board/` dan `/board/new/`.

Navigasi landing page mengikuti permintaan terakhir: navbar tetap **Page 4** dengan placeholder semula. Hanya tombol **Jelajahi** di kartu **Food Rescue Request Board** yang mengarah ke `/board/`. Tombol pada kartu tersebut juga tetap tersedia di mobile; kartu fitur lainnya tidak diubah.

Integrasi `origin/main` mempertahankan default bahasa Indonesia dan komponen design system terbaru. Item navbar tanpa tujuan mengikuti komponen baru sebagai tombol `type="button"`. CSS navbar mendukung elemen tombol dan tautan; override gradasi Request Board memakai token `--primary-gradient` yang digunakan design system terbaru. Stylesheet dan script modal, toast, pagination, serta pencarian dari `main` tetap dimuat melalui `base.html`.

- Daftar publik, donor, dan pemilik memakai kartu hijau, badge status, baris detail sejajar, serta pagination.
- Formulir mencakup enam field, petunjuk pengisian, error per field, dan ringkasan error yang dapat diklik.
- Penawaran terbaru hanya ditampilkan kepada pemilik permintaan.
- Konfirmasi hapus memakai dialog native dengan fokus awal pada Batal, Escape untuk menutup, dan pengembalian fokus ke pemicu. Tanpa JavaScript, konfirmasi tetap tersedia melalui `details`.
- Toast sukses tampil 4 detik; peringatan/info/error 6 detik. Hover atau fokus keyboard menghentikan timer; timer dimulai kembali setelah interaksi selesai. Tombol tutup tetap tersedia.
- Grid dua kolom pada desktop berubah menjadi satu kolom pada mobile. Data berasal dari view yang sudah ada; data contoh hanya dipakai oleh skrip pemeriksaan visual.

## File implementasi

- `request_board/templates/request_board/`: daftar, formulir, kartu, notifikasi, dan ikon CTA.
- `static/css/request-board.css`: seluruh gaya Request Board, termasuk penyesuaian navbar yang terbatas pada halaman modul ini.
- `static/js/request-board.js`: peningkatan konfirmasi hapus menjadi dialog.
- `static/request-board/manifest.json`: asal 17 SVG Figma yang disimpan lokal. Logo dan chevron navbar menggunakan aset proyek yang sudah tersedia. Tidak ada URL aset Figma sementara dalam kode runtime.
- `templates/base.html`: menyediakan blok kelas body untuk membatasi penyesuaian navbar pada modul ini.

Gradasi tombol utama memakai ujung hijau yang lebih gelap dari referensi untuk mempertahankan kontras teks yang sebelumnya sudah diperbaiki di proyek. Navigasi, autentikasi, izin aksi, privasi donor, dan CSRF tetap menggunakan implementasi proyek.

## Validasi

Suite `manage.py test request_board --noinput`: 183 pengujian ditemukan; 176 lulus, 7 khusus concurrency PostgreSQL dilewati pada SQLite. Termasuk 2 pengujian browser toast menggunakan Edge.

`verify_ui.py` merender template produksi dengan data sintetis tanpa menulis database. Pemeriksaan mencakup lebar 320, 360, 375, 768, 1024, dan 1440 px; aset termuat, tidak ada overflow horizontal, menu mobile, serta fokus keyboard dan pembatalan dialog. Kasus `long` memeriksa judul, username, lokasi panjang, serta deskripsi tanpa spasi. `geometry.json` menyimpan hasil beserta ukuran gambar yang dirender. PNG desktop/mobile tersedia di direktori ini untuk pemeriksaan visual; bukan aset halaman.

Jalankan ulang dari root proyek:

```powershell
$env:PLAYWRIGHT_CHROMIUM_CHANNEL='msedge'
.venv/Scripts/python.exe manage.py test request_board --noinput
.venv/Scripts/python.exe docs/figma-request-board/verify_ui.py
.venv/Scripts/python.exe docs/figma-request-board/verify_navigation.py
```

Pemeriksaan visual memerlukan Playwright dan Microsoft Edge. Gunakan `--screens guest form --widths 375 1440` untuk memeriksa subset.
