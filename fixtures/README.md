# Data awal SaveBite

`initial_data.json` berisi 75 record, termasuk 60 konten utama:

| Model | Jumlah |
| --- | ---: |
| Akun pengguna contoh | 5 |
| Food Rescue Plan | 15 |
| Jurnal makanan | 15 |
| Resep komunitas | 15 |
| Permintaan bahan | 15 |
| Penawaran permintaan | 10 |

Fixture mengikuti model yang tersedia di checkout ini. Katalog belum memiliki
model terpasang sehingga tidak ada record katalog. `RequestCreationKey` merupakan
data teknis untuk retry pengiriman form, bukan konten awal yang perlu diisi.

## Memuat data

Jalankan dari direktori yang berisi `manage.py`, pada environment database tujuan:

```bash
python manage.py migrate
python manage.py loaddata fixtures/initial_data.json
```

Di PWS, jalankan perintah tersebut dalam environment aplikasi PWS. Memuat fixture
di komputer lokal hanya mengisi database lokal. Tidak perlu menjalankan seeding
pada setiap deployment.

Record konten menggunakan ID 910001 sampai 910015 per model; cadangkan rentang
tersebut untuk fixture ini dan pastikan tidak dipakai data lain sebelum memuatnya
ke database yang sudah berisi konten. `loaddata` memperbarui record dengan ID yang
sama, sehingga pemuatan ulang dapat menimpa perubahan pada konten contoh.

Akun memakai natural key username sehingga relasi tidak mengandalkan ID pengguna
tertentu. Username `seed_ayu`, `seed_bima`, `seed_citra`, `seed_dimas`, dan `seed_eka`
dikhususkan untuk fixture; pemuatan ulang juga mereset atribut akun-akun tersebut.

## Mencoba modul pribadi

Semua akun contoh aktif, tanpa hak staff atau superuser. Password awal tidak bisa
dipakai login; atur password sendiri untuk akun yang ingin digunakan:

```bash
python manage.py changepassword seed_ayu
```

Login sebagai akun tersebut untuk melihat tiga rencana dan tiga jurnal miliknya.
Setiap akun juga memiliki tiga resep dan tiga permintaan. Untuk mencoba penawaran
dari pengguna lain, atur password akun contoh kedua dengan cara yang sama.
Pemuatan ulang fixture menonaktifkan kembali password akun contoh; jalankan
`changepassword` lagi bila diperlukan.

## Variasi dan relasi

- Planner mencakup `ongoing`, `success`, dan `failed`, serta metode `cook`,
  `preserve`, dan `share`.
- Sepuluh rencana selesai memiliki jurnal yang cocok melalui `rescue_plan_id`;
  lima jurnal lain adalah catatan mandiri, bukan penyelesaian rencana ongoing.
- Board mencakup pending, offered, fulfilled, dan satu contoh kedaluwarsa
  (berdasarkan `expires_at`, bukan nilai status tersimpan).
- Setiap permintaan offered/fulfilled memiliki penawaran dari pengguna lain.
  Permintaan fulfilled menunjuk penawaran terpilih pada permintaan yang sama.
- Permintaan aktif tidak memiliki batas kedaluwarsa sehingga tetap bisa dicoba
  pada deployment berikutnya. Tanggal riwayat tetap pada Oktober 2026, sedangkan
  target rencana ongoing pada November 2026; filter tanggal perlu disesuaikan
  jika fixture digunakan di periode lain.
- Resep memiliki bahan dan langkah lengkap. URL gambar dikosongkan agar contoh
  tidak bergantung pada layanan gambar eksternal dan memakai fallback aplikasi.

Fixture hanya memasukkan data; tidak mengubah template, CSS, atau JavaScript.
