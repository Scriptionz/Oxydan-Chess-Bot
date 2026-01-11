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
        
        # --- CONFIG'DEN ELO AYARLARINI ÇEK ---
        # Config'de yoksa varsayılan 2000-3000 arasını kullanır
        self.min_rating = self.config.get("min_rating", 2000)
        self.max_rating = self.config.get("max_rating", 3200)
        
        self.bot_pool = []           # Online botlar
        self.success_list = set()    # Maç kabul eden "dost" botlar
        self.blacklist = {}          # {bot_id: yasak_bitis_zamani}
        self.last_pool_update = 0
        self.pool_timeout = 1800     # 30 dakikada bir liste güncelle
        self.consecutive_429s = 0

        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Filtre Aktif: {self.min_rating}-{self.max_rating} Elo arası rakipler aranacak.")
        except:
            print("[Matchmaker] Kimlik alınamadı, varsayılan kullanılıyor.")

    def _refresh_bot_pool(self):
        """API'yi yormadan online bot listesini günceller."""
        now = time.time()
        if not self.bot_pool or (now - self.last_pool_update > self.pool_timeout):
            try:
                print("[Matchmaker] Lichess'ten online botlar taranıyor...", flush=True)
                stream = self.client.bots.get_online_bots()
                # API limitleri için ilk 50 botu çek ve karıştır
                online_bots = list(itertools.islice(stream, 50))
                self.bot_pool = [b.get('id') for b in online_bots if b.get('id')]
                random.shuffle(self.bot_pool) 
                self.last_pool_update = now
            except Exception as e:
                print(f"[Matchmaker] Havuz hatası: {e}")
                time.sleep(60)

    def _check_target_rating(self, target_id):
        """Hedef botun Elo'sunun belirlenen aralıkta olup olmadığını kontrol eder."""
        try:
            user_data = self.client.users.get_public_data(target_id)
            perfs = user_data.get('perfs', {})
            
            # Botun en yüksek olduğu (Blitz veya Bullet) ratingi al
            blitz = perfs.get('blitz', {}).get('rating', 0)
            bullet = perfs.get('bullet', {}).get('rating', 0)
            rapid = perfs.get('rapid', {}).get('rating', 0)
            
            max_r = max(blitz, bullet, rapid)
            
            if self.min_rating <= max_r <= self.max_rating:
                return True, max_r
            return False, max_r
        except:
            return False, 0

    def _get_valid_target(self):
        """Elo filtresinden geçen uygun bir rakip seçer."""
        self._refresh_bot_pool()
        now = datetime.now()
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}
        
        # Havuzdan rastgele botları dene
        tried_count = 0
        for target in self.bot_pool:
            if tried_count > 10: break # Tek seferde çok fazla profil sorgulama (429 riski)
            
            if target.lower() == self.my_id.lower() or target in self.blacklist:
                continue
            
            tried_count += 1
            is_suitable, rating = self._check_target_rating(target)
            
            if is_suitable:
                print(f"[Matchmaker] Uygun rakip bulundu: {target} ({rating} Elo)")
                return target
            else:
                # Aralığın dışındaysa 12 saat boyunca kara listeye al (tekrar sorma)
                self.blacklist[target] = now + timedelta(hours=12)
                # print(f"[Matchmaker] {target} ({rating} Elo) aralık dışı, elendi.")
        
        return None

    def start(self):
        if not self.enabled: 
            print("[Matchmaker] Kapalı.")
            return

        while True:
            try:
                # 1. Mevcut Maç Kontrolü
                ongoing = self.client.games.get_ongoing()
                if len(ongoing) >= self.config.get("max_games", 1):
                    time.sleep(30)
                    continue

                # 2. Hedef Seçimi (Elo Filtreli)
                target = self._get_valid_target()
                if not target:
                    print("[Matchmaker] Uygun aralıkta bot bulunamadı, 5 dk bekleniyor.")
                    time.sleep(300)
                    continue

                # 3. Zaman Kontrolü
                tcs = self.config.get("time_controls", ["1+0", "2+1", "3+0"])
                tc = random.choice(tcs)
                t_limit, t_inc = map(int, tc.split('+'))

                # 4. Meydan Okuma Gönderimi
                try:
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    print(f"[Matchmaker] İSTEK GÖNDERİLDİ -> {target} ({tc})")
                    
                    # Başarılı istek sonrası bekleme (İnsan gibi davran)
                    time.sleep(random.randint(60, 120))

                except Exception as e:
                    err = str(e).lower()
                    if "429" in err: raise e
                    
                    # Red yedikse 2 saat sorma
                    self.blacklist[target] = datetime.now() + timedelta(hours=2)
                    time.sleep(random.randint(20, 40))

            except Exception as e:
                if "429" in str(e):
                    self.consecutive_429s += 1
                    wait_time = 900 * self.consecutive_429s 
                    print(f"!!! [429 LIMIT] {wait_time//60} dk uyku modu...")
                    time.sleep(wait_time)
                else:
                    time.sleep(60)
