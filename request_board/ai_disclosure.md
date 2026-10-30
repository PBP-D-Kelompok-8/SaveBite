# AI Disclosure

Saya, **Reshandy Taftazani Aulya (2506547651)**, menggunakan AI dalam pengembangan modul **Food Rescue Request Board** pada proyek SaveBite. Dokumen ini menjelaskan penggunaan AI tersebut.

[README modul](README.md) · [README utama](../Readme.md)

## Alat, tujuan, dan lingkup bantuan

| Alat | Penggunaan pada modul |
| --- | --- |
| **OpenAI Codex** | Terutama sebagai agen pengujian untuk mencari edge case dan bagian yang rawan kesalahan; juga membantu menerjemahkan desain Figma menjadi HTML/template Django dan CSS. |
| **Google Gemini** | Memberikan saran penyusunan desain di Figma, terutama tata letak, hierarki informasi, dan keterbacaan. |
| **Antigravity** | Membantu penyusunan desain di Figma melalui saran perbaikan tampilan dan keterbacaan. |

Saya menggunakan AI untuk mengevaluasi alternatif desain, mengimplementasikan antarmuka, dan memperluas cakupan pemeriksaan. Saya tetap perlu membandingkan rancangan serta hasil implementasi dengan kebutuhan modul dan memeriksa hasilnya.

## Strategi prompt dan alur kerja

Saya memberikan permintaan bantuan secara bertahap: mendiskusikan kebutuhan tampilan dan keterbacaan, menerjemahkan desain Figma ke HTML/CSS, kemudian menguji perilaku dan mencari kasus yang mungkin terlewat. Masalah yang terlihat pada hasil implementasi menjadi masukan untuk penyesuaian berikutnya.

Berikut ringkasan maksud instruksi yang digunakan, **bukan salinan verbatim percakapan atau arsip prompt**:

- **Desain:** meminta saran susunan komponen, hierarki teks, serta cara meningkatkan keterbacaan halaman modul di Figma.
- **Implementasi:** meminta bantuan mengubah rancangan Figma menjadi HTML/template Django dan CSS yang sesuai dengan struktur proyek.
- **Pengujian:** meminta Codex melakukan pengujian mendalam untuk menemukan edge case dan bagian rawan kesalahan, lalu membantu menelusuri masalah yang ditemukan.

Pada kode modul, pemeriksaan yang relevan meliputi input tidak valid, hak akses berdasarkan kepemilikan, penawaran ganda, perubahan status, kedaluwarsa, dan pengiriman ulang setelah gangguan jaringan. Keberadaan tes untuk suatu kasus tidak berarti semua kemungkinan kesalahan telah tertutup atau semua tes selalu dijalankan di setiap lingkungan.

## Keterbatasan AI dan contoh koreksi

Selama pengembangan, saya menemukan hasil bantuan AI yang memerlukan peninjauan ulang pada bagian berikut:

| Temuan | Keterbatasan yang terlihat | Arah koreksi dan pemeriksaan |
| --- | --- | --- |
| Konfirmasi hapus yang seharusnya berupa popup justru mengikuti posisi kartu. | Struktur HTML/CSS yang dihasilkan belum tentu menghasilkan perilaku overlay yang dimaksud desain, meskipun tombol dapat dirender. | Perilaku perlu diarahkan kembali menjadi dialog di atas halaman. Implementasi saat ini menyediakan peningkatan dialog melalui JavaScript; pemeriksaan browser diperlukan untuk memastikan posisi, pembatalan, dan fokusnya. |
| Beberapa bagian tampilan kurang terbaca. | Saran visual dan hasil konversi Figma belum tentu menjaga keterbacaan saat diterapkan pada halaman sebenarnya. | Bagian bermasalah ditinjau dan menjadi dasar penyesuaian tampilan, termasuk hierarki teks serta kontras teks terhadap latar. Hasil perlu dibandingkan lagi pada ukuran layar berbeda. |

**Pemeriksaan manual yang saya lakukan** mencakup mengenali ketidaksesuaian popup dengan perilaku yang saya harapkan dan menemukan bagian yang kurang terbaca, lalu menjadikannya masukan revisi. Dalam memperbaiki implementasi, saya tetap menggunakan bantuan AI; tidak seluruh perubahan kode pada kasus tersebut saya tulis manual tanpa AI.

Kasus tersebut menunjukkan bahwa hasil AI tidak cukup dinilai dari kode yang terlihat benar atau halaman yang berhasil dimuat. Ketepatan interaksi dan keterbacaan tetap perlu diperiksa secara visual. Pengujian berbantuan AI juga dapat melewatkan kasus yang belum dimodelkan; kelulusan tes bukan jaminan aplikasi bebas kesalahan.

## Validasi dan tanggung jawab

Modul menyediakan pengujian Django dan browser untuk memeriksa aturan bisnis serta interaksi. Panduan menjalankan tes ada pada [README modul](README.md#pengujian). Tes browser memerlukan dependensi tambahan, sedangkan pengujian konkurensi PostgreSQL membutuhkan lingkungan yang sesuai; hasil SQLite tidak membuktikan penguncian baris PostgreSQL.

Saya juga menggunakan bantuan AI untuk menyusun dokumentasi ini, termasuk memeriksa struktur kode dan merapikan penjelasan. Riwayat penggunaan alat dan contoh temuan berasal dari pengalaman yang saya sampaikan. Contoh strategi prompt di atas merupakan rangkuman, bukan rekonstruksi kutipan yang tidak tersedia.

Saya tetap bertanggung jawab atas pemahaman kode, keputusan desain, pemeriksaan hasil, dan isi dokumentasi yang saya serahkan.
