import time
import random
import itertools
from datetime import datetime, timedelta

class Matchmaker:
    def __init__(self, client, config):
        self.client = client
        self.config_all = config
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", True)
        self.my_id = "oxydan"
        
        # Elo Sınırları
        self.min_rating = self.config.get("min_rating", 2000)
        self.max_rating = self.config.get("max_rating", 3200)
        
        self.bot_pool = []           # Çevrimiçi botlar
        self.blacklist = {}          # {bot_id: yasak_bitis_zamani}
        self.last_pool_update = 0
        self.pool_timeout = 1800     # 30 dakikada bir liste tazele
        self.consecutive_429s = 0

        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Sistem Hazır. Hedef: {self.min_rating}-{self.max_rating} Elo.")
        except:
            print("[Matchmaker] Kimlik doğrulanamadı, varsayılan ID kullanılıyor.")

    def _refresh_bot_pool(self):
        """Lichess'ten online bot listesini çeker."""
        now = time.time()
        if not self.bot_pool or (now - self.last_pool_update > self.pool_timeout):
            try:
                print("[Matchmaker] Bot listesi güncelleniyor...", flush=True)
                stream = self.client.bots.get_online_bots()
                online_bots = list(itertools.islice(stream, 50))
                self.bot_pool = [b.get('id') for b in online_bots if b.get('id')]
                random.shuffle(self.bot_pool) # Her seferinde farklı sıra
                self.last_pool_update = now
            except Exception as e:
                print(f"[Matchmaker] Liste çekilemedi: {e}")
                time.sleep(60)

    def _check_target_rating(self, target_id):
        """Botun profilini inceler ve Elo'sunu kontrol eder."""
        try:
            user_data = self.client.users.get_public_data(target_id)
            # Banlı veya kapalı botları ele
            if user_data.get('tosViolation') or user_data.get('disabled'):
                return False, 0
                
            perfs = user_data.get('perfs', {})
            # Blitz, Bullet veya Rapid'den en yükseğini baz al
            max_r = max(
                perfs.get('blitz', {}).get('rating', 0),
                perfs.get('bullet', {}).get('rating', 0),
                perfs.get('rapid', {}).get('rating', 0)
            )
            return (self.min_rating <= max_r <= self.max_rating), max_r
        except:
            return False, 0

    def _get_valid_target(self):
        """Hem Elo hem de kara liste kontrolü yaparak rakip seçer."""
        self._refresh_bot_pool()
        now = datetime.now()
        
        # Süresi dolan yasakları temizle
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}
        
        tried_count = 0
        for target in self.bot_pool:
            if tried_count > 10: break # API'yi yormamak için limit
            
            # Kendimiz veya kara listedekileri atla
            if target.lower() == self.my_id.lower() or target in self.blacklist:
                continue
            
            tried_count += 1
            is_suitable, rating = self._check_target_rating(target)
            
            if is_suitable:
                print(f"[Matchmaker] Hedef kilitlendi: {target} ({rating} Elo)")
                return target
            else:
                # Kriter dışı botu 12 saat boyunca bir daha sorma
                self.blacklist[target] = now + timedelta(hours=12)
        
        return None

    def start(self):
        if not self.enabled: return

        while True:
            try:
                # 1. Devam eden maç var mı?
                ongoing = self.client.games.get_ongoing()
                if len(ongoing) >= self.config.get("max_games", 1):
                    time.sleep(30)
                    continue

                # 2. Uygun bir rakip bul
                target = self._get_valid_target()
                if not target:
                    time.sleep(60)
                    continue

                # 3. Rastgele zaman kontrolü seç
                tcs = self.config.get("time_controls", ["1+0", "2+1", "3+0", "5+0"])
                tc = random.choice(tcs)
                t_limit, t_inc = map(int, tc.split('+'))

                # 4. Meydan oku
                try:
                    # KRİTİK: İsteği gönderdiğimiz an botu kara listeye alıyoruz.
                    # Kabul etse de etmese de 1 saat boyunca ona tekrar istek atmayacak.
                    self.blacklist[target] = datetime.now() + timedelta(minutes=60)
                    
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    print(f"[Matchmaker] -> {target} ({tc}) için meydan okuma gönderildi.")
                    
                    # Bir sonraki işlemden önce bekle (İnsan gibi davran)
                    time.sleep(random.randint(60, 150))

                except Exception as e:
                    if "429" in str(e): raise e
                    # Red yedikse veya hata olduysa o botu 2 saatlik cezaya at
                    self.blacklist[target] = datetime.now() + timedelta(hours=2)
                    time.sleep(15)

            except Exception as e:
                if "429" in str(e):
                    self.consecutive_429s += 1
                    wait_time = 900 * self.consecutive_429s
                    print(f"!!! [API LIMIT] {wait_time//60} dakika bekleniyor...")
                    time.sleep(wait_time)
                else:
                    print(f"[Matchmaker] Hata: {e}")
                    time.sleep(30)
