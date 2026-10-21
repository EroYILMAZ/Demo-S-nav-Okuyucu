# Sınav Okuyucu

Telefonla çekilen sınav kağıdı fotoğraflarından **öğrencinin numarasını, adını ve her sorudan aldığı puanı** okur, Excel'e aktarır.
**Tamamen çevrimdışı çalışır.** Okumayı bilgisayarınızda çalışan yerel bir yapay zekâ modeli (Ollama + Qwen3-VL) yapar.
Fotoğraflar yalnızca telefondan bilgisayara, ev/okul Wi-Fi'si içinden gider; internete çıkmaz.

İki parçadan oluşur:
- **Bilgisayar programı:** Masaüstündeki **Sınav Okuyucu** simgesi. Kendi penceresinde açılır, okumayı yapar.
- **Android uygulaması:** Telefona kurulur. Kağıtların fotoğrafını çeker, puanları gösterir, Excel'i telefona indirir.

## Kullanım
1. Bilgisayarda masaüstündeki **Sınav Okuyucu** simgesine çift tıklayın.
2. **Telefona uygulamayı kurun (bir kez):** Programın ana sayfasındaki QR kodu Android telefonun kamerasıyla okutun ve **SinavOkuyucu.apk** dosyasını indirip açın.
   Telefon "bilinmeyen uygulama" uyarısı verirse, tarayıcıya (Chrome) **"Bu kaynaktan izin ver"** deyin. Play Koruma uyarı verirse **"Yine de yükle"** seçin.
3. Telefonda **Sınav Okuyucu** uygulamasını açın. Telefon bilgisayarla **aynı Wi-Fi'de** ise bilgisayarı kendisi bulur.
   Bulamazsa bilgisayar programında yazan adresi (ör. `192.168.1.106:8000`) uygulamaya girin.
   **İlk bağlantıda** telefon 6 haneli bir kod ister: kod, bilgisayardaki programın ana sayfasında (sağdaki “Telefon uygulaması”
   bölümünde) yazar. Bu sayede okul ağındaki başka biri öğrenci fotoğraflarına ve puanlara erişemez. Kod her telefon için bir kez girilir;
   ana sayfadaki “bağlantıları kes” ile bütün telefonların bağlantısı kesilebilir.
4. **Sınav oluşturun:** Sınıf, ders, soru sayısı, her sorunun tam puanı ve **bir kağıdın kaç sayfa olduğu**.
   Sayfa sayısı doğruysa fotoğraflar sırayla aynı öğrenciye bağlanır (2 sayfa → her 2 fotoğraf bir öğrenci); “Sayfa ekle” gerekmez.
5. **Sınıf listesi (bir kez):** “Sınıf listesi”ne e-Okul ya da Excel'den kopyaladığınız listeyi yapıştırın. Liste o sınıf için hatırlanır;
   aynı sınıfın sonraki sınavlarına kendiliğinden eklenir. El yazısı ad/numara hataları düzelir, kağıdı gelmeyen öğrenciler görünür.
6. **Şablonu işaretleyin (en doğru okuma, bir kez ~1 dakika):** Sınav sayfasında **Şablon**'a basın. Bir kağıdın fotoğrafında
   (mümkünse doldurulmamış boş bir kağıt) her sorunun **puanının yazıldığı yeri** sürükleyerek kutu içine alın; bir kutu çizince
   sıradaki soruya geçilir. Kutular biraz geniş olsun ama komşu sorunun puanını içermesin.
   Sonra her kağıt şablona otomatik oturtulur ve her soru ayrı ayrı, yakından birkaç kez okunup oylanır: puanın hangi soruya ait olduğu
   karışmaz, fotoğraf eğik ya da kırpık olsa da olur. Şablon “Başka şube için kopyala” ile diğer şubelere de geçer.
   Gerçek bir 10-C kağıdının farklı açıdan çekilmiş fotoğrafında şablonsuz okuma 10 sorudan 5'ini, şablonlu okuma 10'unu doğru okudu.
   (Şablonlu okuma daha yavaştır: kağıt başına 10–20 dakika. Kağıtları ekleyip bilgisayarı çalışır bırakın.)
7. **Kağıtları ekleyin.** Üç yol var, en hızlısını seçin:
   - **Telefonla seri çekim (önerilir):** “Kağıtları çek”e basın. Kamera açık kalır. Kağıdı koyup elinizi çekin; görüntü sabitlenince
     fotoğraf **kendiliğinden çekilir** (ses gelir), arka planda gönderilir. Sıradaki kağıdı koyun. Bulanık çıkarsa kendisi yeniden çeker.
     Telefonu bir bardağa/standa dayamak işi çok hızlandırır. Elle çekmek için deklanşöre ya da **ses tuşuna** basın; yanlış çekimi **Geri al** ile silin.
     Üst satırda “Sıradaki: 5. öğrenci · sayfa 2/2” yazar. Bitince **Bitti**.
   - **Bir fotoğrafta 2 ya da 4 kağıt:** Sınav sayfasının üstündeki **Fotoğrafta 1 · 2 · 4 kağıt** seçimiyle kağıtları masaya
     yan yana (2) ya da 2×2 (4) dizip tek fotoğraf çekebilirsiniz; program her kağıdı ayrı sayfa olarak keser
     (bkz. “Bir fotoğrafta birden çok kağıt”).
   - **Fotokopi makinesinden PDF:** Kağıt destesini fotokopinin besleyicisine koyup tek bir PDF taratın. PDF'i bilgisayardaki pencereye
     sürükleyip bırakın (ya da “Fotoğraf / PDF ekle”). Sayfalar bölünür ve sırayla öğrencilere ayrılır. Görüntü en net bu yolla olur.
   - **Galeriden / klasörden:** Önceden çekilmiş fotoğrafları seçin ya da pencereye sürükleyin; çekim sırasına göre gruplanır.
   Aynı dosya yanlışlıkla iki kez eklenirse atlanır.
8. Okuma bilgisayarda arka planda sürer. Üstte **tahmini kalan süre** yazar. Okuma motoru bir an kapanırsa kağıtlar hataya düşmez,
   motor açılınca kendiliğinden okunur.
9. **Kontrol edin:** “Kontrol” filtresi yalnızca sarı işaretli kağıtları gösterir. Küçük resme tıklayınca fotoğraf ve puanlar yan yana açılır;
   **Onayla** sizi kontrol bekleyen sonraki kağıda götürür. “… öğrencinin kağıdı yok” yazısına tıklayınca kimlerin eksik olduğunu görürsünüz.
   Kontrol ekranında “Kağıtta yazan toplam” yanlış okunduysa düzeltebilir ya da uyarıyı kaldırabilirsiniz.
10. **Excel:** Bilgisayarda “Farklı kaydet” penceresi açılır; telefonda İndirilenler klasörüne kaydedilir. Henüz okunmamış
    kağıtlar Excel'de durumlarıyla yer alır.
11. Aynı sınavı başka şubeye uygulayacaksanız: **⋯ → Başka şube için kopyala** (sorular, puanlar, sayfa sayısı ve şablon kopyalanır).
12. **⋯** menüsünde: okunamayan kağıtları toplu yeniden okuma, uyarısız kağıtları toplu onaylama, kağıt fotoğraflarını silme (KVKK; puanlar kalır).

## Başka bir öğretmene/bilgisayara vermek
Yapay zekâ modeli dahil tek parça paket oluşturun (internet gerekmez):

```
powershell -ExecutionPolicy Bypass -File araclar\paket_olustur.ps1
```

Paket `%USERPROFILE%\SinavOkuyucu-Paket` klasöründe oluşur (~12 GB: program + Ollama + 8B ve 4B modeller).
USB bellek ya da harici diskle taşıyın; alan kişi **KUR.bat**'a çift tıklar, o kadar. Belleği 12 GB'tan az bilgisayarlarda
program kendiliğinden 4B modele geçer. Öğrenci verileri pakete girmez (veriler `%LOCALAPPDATA%\SinavOkuyucu` altındadır).

Bilgisayar programının penceresini kapatınca okuma da durur. Kağıt okuturken programı açık tutun.

## Telefon bağlanamıyorsa
- Telefon ve bilgisayar **aynı ağda** olmalı. "Misafir" Wi-Fi ağları cihazların birbirini görmesini çoğu zaman engeller.
- **Güvenlik duvarı:** Program ilk açıldığında Windows izin sorar, **İzin ver** deyin. Sonradan izin vermek için:
  Windows Güvenliği → Güvenlik duvarı ve ağ koruması → *Uygulamanın güvenlik duvarından geçmesine izin ver* → *Ayarları değiştir* → listede **SinavOkuyucu** için kutuları işaretleyin.
- Öneri: Bilgisayarın ağı şu an **"Genel"** olarak tanımlı. Evde/okulda **Ayarlar → Ağ ve İnternet → Ethernet (veya Wi-Fi) → Ağ profili türü → Özel** yapmanız daha güvenli ve doğrudur.

## İyi sonuç için
- Kağıdı düz zemine koyun. Kağıdın tamamı kadrajda olsun, gölge ve parlama olmasın.
- Puanları sorunun yanına (sağ boşluğa) ya da üstteki puan tablosuna net yazın. Toplamı da yazarsanız uygulama kontrol eder.
- **Kağıdın toplamını yazın** (ör. sağ üstte çember içinde): Okunan puanların toplamı tutmazsa uygulama kağıdı otomatik olarak yeniden okur ve hatayı çoğu zaman kendisi düzeltir.
- Yanlış okuma çoksa: ⚙ Ayarlar → "Fotoğraf çözünürlüğü" değerini 1800'e çıkarın (daha yavaş, daha doğru).
- Varsayılan model `qwen3-vl:8b-instruct` (en doğru). Daha hızlı ama daha çok hata yapan okuma için ⚙ Ayarlar'dan `qwen3-vl:4b-instruct` seçebilirsiniz.

## Kurşun kalem neden okunmuyor?
Puanlar ve kağıdın toplamı **yalnızca renkli tükenmez kalemden** (mavi, kırmızı, yeşil, mor) okunur. Kurşun kalemle
sonradan yazılan bir rakam (ör. öğretmenin “5”inin önüne eklenen bir “1”) okumaya hiç girmez; sistem böyle kandırılamaz.
- Program her fotoğrafta mürekkebin rengini ölçer. Kurşun kalem, siyah kalem ve basılı yazı fotoğrafta aynı renksiz gridir;
  gerçek kağıtta ölçüldü: öğrencinin kurşun kalemi basılı yazıdan bile koyu çıkabiliyor, yani koyuluğa bakarak ayırmak mümkün
  değil. Renkli mürekkep ise (en koyu lacivert bile) belirgin bir renk taşır. Renksiz her şey silinir, model yalnızca kalanı görür.
- **Puanları siyah kalemle veriyorsanız** ⚙ Ayarlar'dan “Puanları yalnızca renkli tükenmez kalemden oku” seçeneğini kapatın
  (yoksa kutular “renkli kalemle yazılmış puan yok” diye kontrole düşer).
- **Ad ve numara her kalemle okunur:** öğrenciler adını çoğu zaman kurşun kalemle yazar (denediğimiz kağıtta da öyle) ve ad,
  bir puanı yükseltemez.
- Şablonsuz okumada basılı soru numaralarının görünmesi gerektiği için renksiz izler silinmez, soluklaştırılır; ayrıca okunan
  her puanın renkli mürekkepte de bulunduğu denetlenir, bulunmayan puan “kurşun kalemle yazılmış olabilir” diye kontrole düşer.
- Deneme: öğretmenin yuvarlak içindeki mavi “5”inin önüne, öğrencinin kendi kurşun kalem yazısından bir “1” eklendi (“15”).
  Eski okuma dört bakıştan birinde “15” okudu; yeni okuma dördünde de “5” okudu. Süzgeçle gerçek kağıt
  20 kutudan 20'sini doğru okudu (süzgeçsiz 19/20 idi).

## Bir fotoğrafta birden çok kağıt
Sınav sayfasındaki **Fotoğrafta 1 · 2 · 4 kağıt** seçimi o sınav için hatırlanır; telefonun seri çekimi ve galeri de bunu kullanır.
- 2 kağıt: yan yana. 4 kağıt: 2×2 (iki üstte, iki altta). Aralarında biraz boşluk bırakın; koyu bir masa en iyisidir.
- Kağıtlar **soldan sağa, yukarıdan aşağı** sırayla okunur. 2 sayfalık sınavda bir öğrencinin sayfaları sırayla gelsin
  (4'lü çekimde üst satır bir öğrenci, alt satır diğeri).
- Kağıtlar birbirine değiyorsa ya da masa da beyazsa fotoğraf eşit parçalara bölünür ve program bunu bildirir.
- Seri çekimde **Geri al**, son fotoğraftaki bütün kağıtları birlikte geri alır.
- Çoklu çekimde fotoğraf küçültülmeden gönderilir (her kağıt yeterince net kalsın). Şablon işaretlemek özellikle önerilir.
- Ölçülen doğruluk (gerçek 10-C kağıdının iki farklı çekimi, 20 puan kutusu, şablonlu okuma, 12 MP fotoğraf):
  tek kağıt **20/20**, 2 kağıt **20/20**, 4 kağıt **19/20** (dikey ve yatay çekimde aynı). 4'lü çekimdeki tek hata, tek kağıtta da
  zor okunan koyu lacivert bir “10”du; okumalar birbirini tutmadığı için kağıt kontrole düştü (sessizce yanlış yazılmadı).
  En iyi sonuç için **2'li** çekim önerilir; 4'lüde kağıtlar daha küçük göründüğü için soluk ya da çok koyu yazılmış rakamlar
  daha sık kontrole düşer.

## Hangi kağıtlar kontrole düşer?
Yüzdelik bir “eminlik” puanı yoktur. Kağıt, ancak gerekçesi yazılı bir uyarı varsa sarı işaretlenir ve “Kontrol” listesine düşer;
nedeni satırın altında ve hücrenin üzerine gelince yazar:
- **Okumalar farklı:** Her puan kutusu birkaç farklı kırpımla ayrı ayrı okunur. Okumalardan biri bile farklıysa ek okumalar
  yapılır ve hücre kontrole düşer (ör. “Okumalar farklı: 4 / 4 / 1 / 4”).
- **Puan bulunamadı**, **tam puandan büyük**, **hangi soruya ait olduğu belirsiz**, **renkli kalemde görülmedi**.
- **Kağıttaki toplam** okunan puanların toplamıyla tutmuyor.
- **Öğrenci:** numara okunamadı, listede yok, iki kağıtta aynı numara, numara ile isim çelişiyor ya da öğrenci listeden tahmin edildi.
- Sayfa eksik ya da fotoğraf şablona oturtulamadı.

Bir hücreyi elle düzeltirseniz o hücrenin uyarısı kalkar. **Onayla** ile kağıt kontrol listesinden çıkar.

## Sınıf listesiyle öğrenci bulma
Sınıf listesi varsa, numara ve isim listeyle karşılaştırılır:
- Numara listede var ve isim benziyorsa → öğrenci kesin bulunur (el yazısı isim kötü okunsa bile).
- Numara okunamadıysa, listede yoksa ya da isimle çelişiyorsa → model kağıdın başlığına bir kez daha, bu kez
  **sınıf listesiyle birlikte** bakar ve listeden öğrenciyi seçer. Örneğin numara “347” diye yanlış okunmuşsa, liste
  karşılaştırması 247 Deniz Kaya'yı bulur.
- Seçim belirsizse kağıt kontrole düşer ve not ikinci adayı gösterir (ör. “Diğer olasılık: 247 Deniz Kaya”).
- Sınıf listesini kağıtlar okunduktan **sonra** eklerseniz, öğrencisi belirsiz kağıtlar puanları yeniden okunmadan,
  yalnızca başlıkları listeyle karşılaştırılarak düzeltilir.

## Nasıl okuyor?
1. Ad, numara ve toplam yalnızca sayfanın üst kısmından, yakın planda okunur.
2. İki sütunlu sayfalar otomatik algılanır ve her sütun ayrı, yakın planda okunur.
3. İki okuma birbirini tutmazsa ya da toplam tutmuyorsa hücre sarı işaretlenir.
Gerçek bir 10-C matematik kağıdında (2 sayfa, iki sütun, çok karalama) eski sürüm 10 sorudan 7'sini doğru okuyordu; yeni sürüm 10'unu da doğru okudu.

## Teknik notlar
- Veriler (sınavlar, fotoğraflar) OneDrive'a eşitlenmesin diye `%LOCALAPPDATA%\SinavOkuyucu\data` altında tutulur.
- Program `%LOCALAPPDATA%\Programs\SinavOkuyucu` altına kuruludur.
- Bu bilgisayarın NVIDIA sürücüsü (560.94) Ollama'nın CUDA sürümüyle uyumsuz. Ekran kartı Vulkan üzerinden kullanılıyor (program bunu kendisi ayarlar).
- Yeniden derlemek için:
  - Bilgisayar programı: `araclar\exe_derle.ps1`
  - Android uygulaması: `araclar\apk_derle.ps1` (Android araçları `%USERPROFILE%\android-build` altında)
- Başka bir bilgisayara kurulum için önce `kurulum.bat` dosyasını çalıştırın; Ollama'yı ve modeli kurar.
- `araclar\dogruluk_testi.py`: bilinen kağıtlarla okuma doğruluğu testi (gerçek kağıtlar `araclar\kagitlar.json` içinde).
- `araclar\sunucu_testi.py`: gruplama, seri çekim oturumu, geri alma, PDF ve sınıf listesi testleri (okuma motoru çalışmaz, birkaç saniye sürer).
- Seri çekimin otomatik deklanşör eşikleri `SeriCekimActivity.java` başındaki sabitlerdedir (MOVE_T, STEADY_T, CHANGE_T, STEADY_MS).
