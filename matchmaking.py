import time
import random
import itertools
from datetime import datetime, timedelta

class Matchmaker:
    def __init__(self, client, config):
        self.client = client
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", True)
        self.my_id = "oxydan"
        
        # --- GELİŞMİŞ BELLEK VE GÜVENLİK ---
        self.bot_pool = []           # Online botlar
        self.success_list = set()    # Daha önce maç kabul eden "dost" botlar
        self.blacklist = {}          # {bot_id: yasak_bitis_zamani}
        self.last_pool_update = 0
        self.pool_timeout = 1800     # 30 dakikada bir liste güncelle (API dostu)
        self.consecutive_429s = 0    # Üst üste alınan 429 hatası sayısı

        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Kimlik Doğrulandı: {self.my_id}")
        except:
            print("[Matchmaker] Kimlik alınamadı, varsayılan kullanılıyor.")

    def _refresh_bot_pool(self):
        """API'yi yormadan online bot listesini günceller."""
        now = time.time()
        if not self.bot_pool or (now - self.last_pool_update > self.pool_timeout):
            try:
                print("[Matchmaker] Lichess'ten taze bot listesi çekiliyor...", flush=True)
                # Sadece ilk 40 botu al (API limitlerini zorlamamak için)
                stream = self.client.bots.get_online_bots()
                online_bots = list(itertools.islice(stream, 40))
                self.bot_pool = [b.get('id') for b in online_bots if b.get('id')]
                random.shuffle(self.bot_pool) # Her seferinde aynı botlara gitme
                self.last_pool_update = now
            except Exception as e:
                print(f"[Matchmaker] Havuz hatası: {e}")
                time.sleep(60)

    def _get_valid_target(self):
        """Önce eski dostları, sonra yeni rakipleri seçer."""
        self._refresh_bot_pool()
        now = datetime.now()
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}
        
        # 1. Öncelik: Daha önce maç yapmış olduğumuz botlar online mı?
        friends = [b for b in self.success_list if b in self.bot_pool and b not in self.blacklist]
        if friends:
            return random.choice(friends)
            
        # 2. Öncelik: Diğer online botlar
        targets = [b for b in self.bot_pool 
                   if b.lower() != self.my_id.lower() and b not in self.blacklist]
        
        return random.choice(targets) if targets else None

    def start(self):
        print(f"[Matchmaker] Avcı V8 aktif. Bilgisayar kapatılabilir.", flush=True)
        if not self.enabled: return

        while True:
            try:
                # 1. Mevcut Maç ve Bekleyen İstek Kontrolü
                # Çok fazla bekleyen istek olması da 429 tetikler
                ongoing = self.client.games.get_ongoing()
                if len(ongoing) >= self.config.get("max_games", 1):
                    time.sleep(30)
                    continue

                # 2. Hedef Seçimi
                target = self._get_valid_target()
                if not target:
                    time.sleep(120)
                    continue

                # 3. Akıllı Zaman Kontrolü (Bullet/Blitz karışık)
                tcs = self.config.get("time_controls", ["1+0", "2+1", "3+0", "3+2", "5+0"])
                tc = random.choice(tcs)
                t_limit, t_inc = map(int, tc.split('+'))

                # 4. Meydan Okuma Gönderimi
                try:
                    print(f"[Matchmaker] Hedef: {target} ({tc})", flush=True)
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    print(f"[Matchmaker] İstek gönderildi: {target}")
                    self.success_list.add(target) # Başarılı istek listesine ekle
                    self.consecutive_429s = 0     # Sayacı sıfırla
                    
                    # Başarılı istek sonrası bekleme (İnsan gibi)
                    time.sleep(random.randint(45, 75))

                except Exception as e:
                    err = str(e).lower()
                    if "429" in err:
                        raise e # Dış except bloğuna fırlat
                    
                    # Bot reddettiyse veya meşgulse kara listeye al
                    print(f"[Matchmaker] {target} reddetti/meşgul. 1 saat pas geçiliyor.")
                    self.blacklist[target] = datetime.now() + timedelta(hours=1)
                    # Red yeyince hemen yeni birine gitme (Lichess spam sanmasın)
                    time.sleep(random.randint(10, 20))

            except Exception as e:
                error_msg = str(e).lower()
                if "429" in error_msg:
                    self.consecutive_429s += 1
                    # Her 429'da bekleme süresini katla (15dk, 30dk, 45dk...)
                    wait_time = 900 * self.consecutive_429s 
                    print(f"!!! [429 LIMIT] Lichess engeli. {wait_time//60} dk tam sessizlik...", flush=True)
                    time.sleep(wait_time)
                else:
                    print(f"[Matchmaker] Hata: {e}")
                    time.sleep(60)
