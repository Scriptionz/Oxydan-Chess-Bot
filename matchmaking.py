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
        
        # --- GELİŞMİŞ BELLEK AYARLARI ---
        self.bot_pool = []           # Çekilen online botlar
        self.last_pool_update = 0    # Son liste güncelleme zamanı
        self.blacklist = {}          # {bot_id: yasak_bitis_zamani}
        self.pool_timeout = 1200     # 20 dakikada bir liste güncelle (API dostu)

        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Kimlik Doğrulandı: {self.my_id}")
        except:
            print("[Matchmaker] Kimlik alınamadı, varsayılan kullanılıyor.")

    def _refresh_bot_pool(self):
        """API'den online bot listesini çeker ve önbelleğe alır."""
        now = time.time()
        if not self.bot_pool or (now - self.last_pool_update > self.pool_timeout):
            try:
                print("[Matchmaker] Lichess'ten taze bot listesi çekiliyor...", flush=True)
                stream = self.client.bots.get_online_bots()
                # İlk 50 botu al, içinden kendini ve kara listedekileri çıkar
                online_bots = list(itertools.islice(stream, 50))
                self.bot_pool = [b.get('id') for b in online_bots if b.get('id')]
                self.last_pool_update = now
                print(f"[Matchmaker] Havuz güncellendi: {len(self.bot_pool)} bot bulundu.", flush=True)
            except Exception as e:
                print(f"[Matchmaker] Havuz güncellenirken hata: {e}")
                self.bot_pool = []

    def _get_valid_target(self):
        """Havuzdan kara listede olmayan rastgele bir bot seçer."""
        self._refresh_bot_pool()
        now = datetime.now()
        
        # Kara listeyi temizle (süresi dolanları çıkar)
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}
        
        # Kendini ve kara listedekileri filtrele
        targets = [b for b in self.bot_pool 
                   if b.lower() != self.my_id.lower() 
                   and b not in self.blacklist]
        
        return random.choice(targets) if targets else None

    def start(self):
        print(f"[Matchmaker] Avcı modu aktif. Durum: {self.enabled}")
        if not self.enabled: return

        while True:
            try:
                # 1. Maç kontrolü
                ongoing = self.client.games.get_ongoing()
                if len(ongoing) >= self.config.get("max_games", 1):
                    time.sleep(30)
                    continue

                # 2. Hedef Seçimi
                target = self._get_valid_target()
                if not target:
                    print("[Matchmaker] Uygun hedef yok, 5 dk bekleniyor...")
                    time.sleep(300)
                    continue

                # 3. Zaman Kontrolü Seçimi
                tcs = self.config.get("time_controls", ["3+2", "5+3", "1+0"])
                tc = random.choice(tcs)
                t_limit, t_inc = map(int, tc.split('+'))

                # 4. Meydan Okuma
                print(f"[Matchmaker] Hedef: {target} ({tc}) | Kara Liste Boyutu: {len(self.blacklist)}")
                try:
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    print(f"[Matchmaker] İstek başarılı: {target}")
                except Exception as e:
                    # Eğer bot meşgulse veya reddettiyse kara listeye al
                    print(f"[Matchmaker] {target} isteği reddetti/meşgul. 1 saat kara listeye alındı.")
                    self.blacklist[target] = datetime.now() + timedelta(hours=1)
                
                # 5. Bekleme (Jitter/Sapma ekleyerek)
                base_interval = self.config.get("challenge_interval", 60)
                jitter = random.randint(-10, 20) # -10 ile +20 sn arası rastgele ekle
                actual_wait = max(30, base_interval + jitter)
                
                print(f"[Matchmaker] Sonraki tarama {actual_wait}sn sonra...")
                time.sleep(actual_wait)

            except Exception as e:
                error_msg = str(e).lower()
                if "429" in error_msg:
                    wait_time = 900 # 15 dakika tam sessizlik
                    print(f"!!! [Matchmaker] LİCHESS ENGELİ (429) !!! {wait_time/60} dk bekleniyor...", flush=True)
                    time.sleep(wait_time)
                else:
                    print(f"[Matchmaker] Genel Hata: {e}")
                    time.sleep(60)
