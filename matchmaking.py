import time
import random
import itertools
import os
from datetime import datetime, timedelta

class Matchmaker:
    def __init__(self, client, config, active_games): 
        self.client = client
        self.config_all = config
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", True)
        self.active_games = active_games  # Ana koddaki set'e referans
        self.my_id = None
        
        self.min_rating = self.config.get("min_rating", 2000)
        self.max_rating = self.config.get("max_rating", 4000)
        self.max_parallel_games = 2 
        
        self.bot_pool = []
        self.blacklist = {}
        self.last_pool_update = 0
        self.pool_timeout = 3600 
        self.consecutive_429s = 0

        self._initialize_id()

    def _initialize_id(self):
        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Sistem Hazır. ID: {self.my_id}")
        except Exception as e:
            print(f"[Matchmaker] Kimlik hatası: {e}")
            self.my_id = "oxydan"

    def _refresh_bot_pool(self):
        now = time.time()
        if not self.bot_pool or (now - self.last_pool_update > self.pool_timeout):
            try:
                stream = self.client.bots.get_online_bots()
                online_bots = list(itertools.islice(stream, 100))
                self.bot_pool = [b.get('id') for b in online_bots if b.get('id') and b.get('id').lower() != self.my_id.lower()]
                random.shuffle(self.bot_pool)
                self.last_pool_update = now
            except Exception as e:
                self._handle_rate_limit(e)

    def _handle_rate_limit(self, error):
        if "429" in str(error):
            self.consecutive_429s += 1
            wait_time = 300 * self.consecutive_429s
            print(f"!!! [API LIMIT] {wait_time}sn uykuda...")
            time.sleep(wait_time)
        else:
            time.sleep(20)

    def _check_target_rating(self, target_id):
        try:
            user_data = self.client.users.get_public_data(target_id)
            if user_data.get('tosViolation') or user_data.get('disabled'):
                return False, 0
            perfs = user_data.get('perfs', {})
            ratings = [perfs.get(c, {}).get('rating', 0) for c in ['blitz', 'bullet', 'rapid']]
            max_r = max(ratings) if ratings else 0
            return (self.min_rating <= max_r <= self.max_rating), max_r
        except:
            return False, 0

    def _get_valid_target(self):
        self._refresh_bot_pool()
        now = datetime.now()
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}
        
        count = 0
        for target in self.bot_pool:
            if count >= 8: break
            if target in self.blacklist: continue
            
            count += 1
            is_ok, r = self._check_target_rating(target)
            if is_ok: return target
            else: self.blacklist[target] = now + timedelta(hours=12)
        return None

    def start(self):
        if not self.enabled: return

        print("[Matchmaker] Döngü başlatıldı. STOP.txt ile durdurulabilir.")

        while True:
            # --- 1. KESİN DURDURMA (STOP.txt) ---
            if os.path.exists("STOP.txt"):
                # STOP.txt varsa hiçbir şey yapma, sadece uyu.
                time.sleep(10)
                continue

            try:
                # 2. SLOT KONTROLÜ
                # Eğer 2 maç zaten varsa (veya fazlası), bekle.
                if len(self.active_games) >= self.max_parallel_games:
                    time.sleep(20)
                    continue

                # 3. İLK HAMLE FRENİ (ABORT KORUMASI)
                # İçeride 1 maç varken 2.yi hemen arama. Motorun hamle yapmasına izin ver.
                if len(self.active_games) > 0:
                    time.sleep(12) 

                # Tekrar STOP kontrolü (Vakit kaybını önlemek için)
                if os.path.exists("STOP.txt"): continue

                # 4. RAKİP BULMA
                target = self._get_valid_target()
                if not target:
                    time.sleep(30)
                    continue

                # 5. MEYDAN OKUMA VE ZORUNLU KİLİT
                tc = random.choice(["1+0", "3+0", "3+2", "5+0"])
                t_limit, t_inc = map(int, tc.split('+'))

                try:
                    self.blacklist[target] = datetime.now() + timedelta(minutes=45)
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    
                    # EN ÖNEMLİ FREN: Davet gittikten sonra sistemi 20 saniye dondur.
                    # Bu sayede 10 kişiye aynı anda davet atması İMKANSIZ.
                    print(f"[Matchmaker] -> {target} Davet edildi. 20sn KİLİT.")
                    time.sleep(20) 

                except Exception as e:
                    print(f"[Matchmaker] Hata: {e}")
                    self.blacklist[target] = datetime.now() + timedelta(hours=2)
                    time.sleep(10)

            except Exception as e:
                self._handle_rate_limit(e)
