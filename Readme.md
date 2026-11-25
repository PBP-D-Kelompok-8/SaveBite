# 🍲 SaveBite: Food Waste Management

*Platform web penanggulangan pemborosan makanan (food waste) berbasis Django untuk mata kuliah Pemrograman Berbasis Platform (PBP), Kelas D, Kelompok 8.*

---

## 🔗 Tautan Penting
- **Deployment (PWS):** [Tautan Deployment PWS](https://reshandy-taftazani-savebite.pws.cs.ui.ac.id)
- **Desain UI/UX (Figma):** [Tautan Prototipe Desain Figma](https://www.figma.com/team_invite/redeem/woFR9NgdX8H0JKkRXPhlUH?t=ktOcTlt7UlLpleuI-21)
- **Repositori Git:** [Tautan Repository Kelompok 8 PBP D](https://github.com/PBP-D-Kelompok-8/SaveBite)

---

## 👥 Anggota Kelompok & Pembagian Modul
**Kelas / Kelompok:** PBP D / Kelompok 8

| Nama | NPM | Modul yang Dikerjakan |
| :--- | :--- | :--- |
| **Muhammad Ilhami Yasya** | 2506593891 | **Modul 1:** Katalog Resep Bahan Sisa *(Integrasi TheMealDB)* |
| **Mutia Muthmainnah** | 2506625230 | **Modul 2:** Food Rescue Planner *(Manajemen Rencana Penyelamatan)* |
| **Silvia Lalita Damayanti** | 2506621863 | **Modul 3:** Resep Komunitas *(Platform Berbagi Resep Sisa)* |
| **Reshandy Taftazani Aulya** | 2506547651 | **Modul 4:** Food Rescue Request Board *(Papan Permintaan Bahan)* |
| **Georgius Satria Adibrata** | 2506589976 | **Modul 5:** Jurnal Jejak Makanan *(Pencatatan Riwayat & Analitik)* |

---

## 📖 Deskripsi Aplikasi & Manfaat bagi Masyarakat

Pemborosan makanan (*food waste*) merupakan salah satu isu lingkungan dan finansial terbesar saat ini. Banyak bahan makanan yang sebenarnya masih layak konsumsi namun terbuang sia-sia karena kurangnya perencanaan atau ide pengelolaannya. 

**SaveBite** hadir sebagai platform web interaktif untuk menanggulangi masalah tersebut. Platform ini membantu pengguna memaksimalkan pemanfaatan bahan makanan dengan menyediakan fitur pembuatan rencana penyelamat makanan, mesin pencari resep darurat berbahan sisa, hingga ruang komunitas untuk saling berbagi resep dan sisa bahan makanan. Dengan mencatat jejak makanan secara konsisten, SaveBite bertujuan membangun kebiasaan konsumsi yang lebih bijak dan berkelanjutan.

---

## 🎯 Target Pengguna & Peran Pengguna (User Roles)

### Target Pengguna
- **Anak Kost & Mahasiswa:** Individu dengan bahan makanan terbatas yang sering kebingungan mengolah sisa bahan di kulkas.
- **Rumah Tangga / Pemula Masak:** Individu yang ingin menghemat pengeluaran belanja harian dan mengurangi limbah organik rumah tangga.

### Peran Pengguna
- **Pengunjung Umum (Guest / Unauthenticated):**
  - Dapat menelusuri katalog resep publik.
  - Dapat melihat *posting board* tawaran/permintaan makanan komunitas.
  - *Tidak dapat* berinteraksi langsung atau mengelola inventaris pribadi.
- **Pengguna Terdaftar (Authenticated User):**
  - Mengelola kulkas/inventaris pribadi dan menyimpan resep favorit.
  - Membagikan resep kreasi sendiri ke komunitas.
  - Membuat atau merespons tawaran/permintaan bahan makanan di *board*.
  - Mengisi dan memantau jurnal jejak makanan harian.

---

## 📦 Rincian Modul (Implementasi CRUD Lengkap)

Setiap modul dikembangkan secara independen dan memenuhi standar implementasi CRUD (*Create, Read, Update, Delete*) pada arsitektur Django (Model-View-Template):

### 1. Katalog Resep Bahan Sisa (TheMealDB API) — Ilham
**Dokumentasi modul:** [README Katalog Resep Bahan Sisa](katalog/README.md).
**Deskripsi:** Modul cerdas untuk mencari inspirasi resep masakan berdasarkan sisa bahan yang hampir kedaluwarsa.
- **Model Dasar:** Menyimpan data resep favorit/bookmark dari API beserta catatan kustomisasi pengguna.
- **Fitur CRUD:**
  - **Create:** Menyimpan (*bookmark*) resep hasil pencarian dari API ke dalam daftar favorit lokal.
  - **Read:** Menampilkan hasil *fetch* pencarian dari API TheMealDB dan membaca daftar resep yang sudah di-bookmark.
  - **Update:** Mengedit catatan atau tips pribadi pada resep yang telah disimpan.
  - **Delete:** Menghapus resep dari daftar bookmark.

### 2. Food Rescue Planner — Mutia
**Deskripsi:** Perencana aksi untuk menyelamatkan bahan makanan dengan target dan metode yang jelas (misal: dimasak, diawetkan).
- **Model Dasar:** Menyimpan nama bahan, jumlah, target tanggal, metode aksi, dan status progres (*On Going, Success, Failed*).
- **Fitur CRUD:**
  - **Create:** Membuat *rescue plan* baru dengan detail nama, jumlah, target, dan metode aksi.
  - **Read:** Melihat daftar *rescue plan* yang aktif beserta status progresnya.
  - **Update:** Mengubah jumlah, target tanggal, metode aksi, atau status (menjadi *Success/Failed*).
  - **Delete:** Menghapus rencana yang sudah dibatalkan atau tidak relevan.
> **Integrasi:** *Rescue plan* yang berstatus *Success* atau *Failed* akan terhubung otomatis ke modul **Jurnal Jejak Makanan** (Geo) untuk pencatatan akhir.

### 3. Resep Komunitas — Lalita
**Deskripsi:** Ruang berbagi (*feed*) tempat pengguna mempublikasikan resep kreasi dari bahan sisa untuk menginspirasi pengguna lain.
- **Model Dasar:** Menyimpan judul resep, deskripsi, daftar bahan sisa, langkah-langkah, dan relasi ke pembuat (*Author*).
- **Fitur CRUD:**
  - **Create:** Menambahkan dan mempublikasikan resep kreasi sendiri.
  - **Read:** Menjelajahi resep-resep yang dibagikan oleh pengguna lain dalam bentuk linimasa (*feed*).
  - **Update:** Mengedit detail resep (judul, bahan, cara masak) yang telah dibuat sendiri.
  - **Delete:** Menghapus resep pribadi dari *feed* komunitas.

### 4. Food Rescue Request Board — Sandy
**Dokumentasi modul:** [README Food Rescue Request Board](request_board/README.md).

**Deskripsi:** Papan interaktif bergaya forum bagi komunitas lokal untuk meminta sisa bahan makanan dalam jumlah kecil agar tidak perlu membeli baru.
- **AI Disclosure:** [AI Disclosure](request_board/ai_disclosure.md).
- **Model Dasar:** Menyimpan detail permintaan bahan, relasi peminta, dan status pemenuhan (*Pending, Terpenuhi*).
- **Fitur CRUD:**
  - **Create:** Memposting permintaan/kebutuhan sisa bahan makanan spesifik.
  - **Read:** Menampilkan papan *board* berisi daftar pencarian bahan makanan dari pengguna lain.
  - **Update:** Interaksi mengubah status; pengguna lain merespons dengan menekan tombol "Saya Punya!", dan pembuat post dapat mengubah status menjadi "Terpenuhi".
  - **Delete:** Menghapus *posting* permintaan jika sudah tidak butuh atau sudah dibeli sendiri.

### 5. Jurnal Jejak Makanan — Geo
**Deskripsi:** Modul rekam jejak pribadi (*tracker*) untuk menghitung dan mengevaluasi persentase makanan yang berhasil diselamatkan atau terbuang.
- **Model Dasar:** Menyimpan log harian berupa nama makanan, status konsumsi (*Terselamatkan/Terbuang*), dan catatan riwayat makanan.
- **Fitur CRUD:**
  - **Create:** Mencatat log riwayat makanan harian beserta status akhirnya.
  - **Read:** Menampilkan log riwayat secara kronologis serta ringkasan persentase keberhasilan (*success rate*).
  - **Update:** Mengedit catatan atau memperbaiki *input* status dan jumlah yang salah.
  - **Delete:** Menghapus baris catatan riwayat dari jurnal.

---

## 🌐 Integrasi API Publik Eksternal

- **[TheMealDB API](https://www.themealdb.com/api.php)**
  - **Fungsi:** Digunakan pada Modul 1 (Katalog Resep) untuk mengambil (*fetch*) katalog data resep makanan secara publik (tanpa token). Sangat berguna untuk memberikan rekomendasi masakan instan berdasarkan sisa bahan pangan yang dipilih oleh pengguna.

---

## 🗄️ Data Awal

Fixture [`fixtures/initial_data.json`](fixtures/initial_data.json) tersedia dengan **75 record**, termasuk **60 konten utama**:

| Data | Jumlah |
| --- | ---: |
| Akun pengguna contoh | 5 |
| Food Rescue Plan | 15 |
| Jurnal makanan | 15 |
| Resep komunitas | 15 |
| Permintaan bahan | 15 |
| Penawaran permintaan | 10 |

Setelah dependensi terpasang, jalankan dari root repositori pada environment database yang ingin diisi:

```sh
python manage.py migrate
python manage.py loaddata fixtures/initial_data.json
```

Untuk mencoba fitur pengguna login, atur password salah satu akun contoh:

```sh
python manage.py changepassword seed_ayu
```

Akun lain yang tersedia adalah `seed_bima`, `seed_citra`, `seed_dimas`, dan `seed_eka`. Semua akun contoh tidak memiliki hak staff atau superuser. Password awal tidak dapat dipakai login; atur password akun kedua untuk mencoba penawaran antar pengguna.

Memuat fixture di komputer lokal hanya mengisi database lokal. Untuk PWS, jalankan pada environment aplikasi PWS; tidak perlu memuat ulang pada setiap deployment. Pemuatan ulang dapat menimpa konten contoh dengan ID yang sama dan menonaktifkan kembali password akun contoh.

Fixture tidak berisi bookmark Katalog Resep; data resep eksternal dicari melalui fitur Katalog. Panduan rentang ID, relasi antarmodul, tanggal contoh, dan penggunaan database yang sudah berisi data tersedia di [dokumentasi data awal](fixtures/README.md).

---

## 🤖 Ai Disclosure
### 1. Katalog Resep Bahan Sisa (Muhammad Ilhami Yasya | 2506593891)

ChatGPT digunakan untuk menghasilkan keseluruhan kode modul Katalog Resep Bahan Sisa serta rancangan antarmuka Figma berdasarkan kebutuhan dan arahan yang saya berikan. Kode yang dihasilkan mencakup backend Django, template HTML, CSS, dan JavaScript.

Saya menentukan kebutuhan fitur dan memberikan arahan selama pengerjaan. Hasilnya kemudian saya tinjau dan uji, termasuk memeriksa logika program, kemungkinan error, serta kesesuaian desain Figma dengan implementasi aplikasi.

**Chatlog pemeriksaan:** [ChatGPT — Pengecekan Logika Repository dan Kesesuaian Desain Figma](https://chatgpt.com/share/6ae4b18e-0ee0-83ec-afd6-ae79bdeac29f)


### 3. Resep Komunitas (Silvia Lalita D. | 2506621863)
Saya menggunakan **ChatGPT** sebagai alat bantu selama pengembangan modul **Resep Komunitas** pada proyek SaveBite. AI digunakan untuk menghasilkan kode awal, membantu menyelesaikan masalah selama pengembangan, memahami error, serta membantu proses penggunaan Git. Kode yang dihasilkan AI tidak selalu langsung sesuai dengan kebutuhan modul. Oleh karena itu, saya melakukan penyesuaian dan modifikasi terhadap kode tersebut agar sesuai dengan kebutuhan dan implementasi yang saya inginkan.
AI digunakan sebagai alat bantu dalam proses pengembangan, sedangkan implementasi akhir tetap disesuaikan dengan kebutuhan modul.
- [ChatGPT — Percakapan terkait pengembangan modul Resep Komunitas](https://chatgpt.com/share/6ae37ce5-c9c4-83ec-9d0b-6db6d04505a2)


### 5. Jurnal Jejak Makanan (Georgius Satria Adibrata)

Tool: ***Claude***

Dipakai Untuk Figma:

Membantu menentukan apa saja yang harus dibuat dalam modul saya, lalu saya membuat sendiri design halaman jurnal dalam Figma.

Mengajarkan saya cara untuk memakai Figma (auto layout, corner radius, dll)

Membuat versi mobile dari desain desktop yang saya kerjakan

Mengerjakan ulang desain desktop saya karena tidak memakai auto layout

Membuat beberapa komponen (cth: form modal, delete confirmation modal) untuk modul saya

Strategi:

Mengirim screenshot lalu saya tanyakan apakah sudah bagus atau belom designnya

Link Chatlog AI: [Claude — Percakapan Figma & Design](https://claude.ai/share/60c08d53-5524-445a-b6bb-a15f2311bd6e)


Tool: ***ChatGPT***

Dipakai Untuk Figma:

Membantu saya dalam mengkonversi dari figma ke HTML dan CSS

Strategi:

Memakai aplikasinya agar bisa langsung ngedit file projectnya

Link Chatlog AI: [ChatGPT — Konversi Figma ke HTML & CSS](https://chatgpt.com/s/cx_6adf2c8aa03c8191b02507c6b16234d4)


Tool: ***ChatGPT***

Dipakai untuk membuat CRUD:

Mencari jika ada bug dalam project

Strategi:

Memakai aplikasinya agar bisa langsung ngedit file projectnya

Link Chatlog AI: [ChatGPT — CRUD & Bug Fixing](https://chatgpt.com/s/cx_6ae4948d4c648191aba2769a9ebe6f86)
