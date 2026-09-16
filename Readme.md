# 🍲 SaveBite: Food Waste Management

*Platform web penanggulangan pemborosan makanan (food waste) berbasis Django untuk mata kuliah Pemrograman Berbasis Platform (PBP), Kelas D, Kelompok 8.*

---

## 🔗 Tautan Penting
- **Deployment (PWS):** [Tautan Deployment PWS](#) *(Akan diperbarui)*
- **Desain UI/UX (Figma):** [Tautan Prototipe Desain Figma](#)
- **Repositori Git:** [https://github.com/PBP-D-Kelompok-8/SaveBite / Repositori](#)

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
**Deskripsi:** Papan interaktif bergaya forum bagi komunitas lokal untuk meminta sisa bahan makanan dalam jumlah kecil agar tidak perlu membeli baru.
- **Model Dasar:** Menyimpan detail permintaan bahan, relasi peminta, dan status pemenuhan (*Pending, Terpenuhi*).
- **Fitur CRUD:**
  - **Create:** Memposting permintaan/kebutuhan sisa bahan makanan spesifik.
  - **Read:** Menampilkan papan *board* berisi daftar pencarian bahan makanan dari pengguna lain.
  - **Update:** Interaksi mengubah status; pengguna lain merespons dengan menekan tombol "Saya Punya!", dan pembuat post dapat mengubah status menjadi "Terpenuhi".
  - **Delete:** Menghapus *posting* permintaan jika sudah tidak butuh atau sudah dibeli sendiri.

### 5. Jurnal Jejak Makanan — Geo
**Deskripsi:** Modul rekam jejak pribadi (*tracker*) untuk menghitung dan mengevaluasi persentase makanan yang berhasil diselamatkan atau terbuang.
- **Model Dasar:** Menyimpan log harian berupa nama makanan, status konsumsi (*Habis/Terbuang*), dan catatan evaluasi.
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

## 🗄️ Strategi Data Awal (Seeding Minimum)
Untuk memenuhi ketentuan *deployment* awal, kelompok akan menyiapkan:
- Berkas *fixture* Django (`fixtures/initial_data.json`) yang memuat minimal 50+ data awal.
- Data ini meliputi variasi contoh *rescue plan*, beberapa resep komunitas awal, daftar log riwayat makanan, serta beberapa *post* permintaan di *board* agar platform langsung memiliki konten yang dapat dilihat dan diinteraksikan saat pertama kali dijalankan oleh *tester* atau dosen.
