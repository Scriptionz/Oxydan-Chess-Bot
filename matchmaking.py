import time
import random
import itertools
from datetime import datetime, timedelta

class Matchmaker:
    def __init__(self, client, config, active_games): 
        self.client = client
        self.config_all = config
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", True)
        self.active_games = active_games  
        self.my_id = None
        
        self.min_rating = self.config.get("min_rating", 2000)
        self.max_rating = self.config.get("max_rating", 4000)
        self.max_parallel_games = 2 
        
        self.bot_pool = []
        self.blacklist = {}
        self.last_pool_update = 0
        self.pool_timeout = 7200 # 1 saatten 2 saate çıkardık (API Ban koruması)
        self.consecutive_429s = 0

        self._initialize_id()

    def _initialize_id(self):
        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Sistem Hazır. ID: {self.my_id}")
        except Exception as e:
            print(f"[Matchmaker] Kimlik doğrulanamadı, 10sn sonra tekrar denenecek...")
            time.sleep(10)

    def _refresh_bot_pool(self):
        now = time.time()
        # API Limitlerini korumak için havuz yenileme süresini uzattık
        if not self.bot_pool or (now - self.last_pool_update > self.pool_timeout):
            try:
                print("[Matchmaker] Bot listesi güncelleniyor...", flush=True)
                time.sleep(random.uniform(2, 5)) # İsteği rastgele geciktir (Bot algılanma koruması)
                stream = self.client.bots.get_online_bots()
                online_bots = list(itertools.islice(stream, 80)) # 100'den 80'e düşürdük
                
                self.bot_pool = [b.get('id') for b in online_bots if b.get('id') and b.get('id').lower() != self.my_id.lower()]
                random.shuffle(self.bot_pool)
                self.last_pool_update = now
                self.consecutive_429s = 0 
            except Exception as e:
                self._handle_rate_limit(e)

    def _handle_rate_limit(self, error):
        if "429" in str(error):
            self.consecutive_429s += 1
            # 10 dk yerine daha agresif bir bekleme: 15, 30, 45 dk...
            wait_time = 900 * self.consecutive_429s 
            print(f"🚨 [KRİTİK API LİMİT] {wait_time//60} dakika tam uyku modu...")
            time.sleep(wait_time)
        else:
            time.sleep(60)

    def _check_target_rating(self, target_id):
        try:
            # API Ban riskine karşı profil sorgusundan önce küçük bir bekleme
            time.sleep(random.uniform(0.5, 1.5))
            user_data = self.client.users.get_public_data(target_id)
            if user_data.get('tosViolation') or user_data.get('disabled'):
                return False, 0
                
            perfs = user_data.get('perfs', {})
            ratings = [perf.get('rating', 0) for cat, perf in perfs.items() if cat in ['blitz', 'bullet', 'rapid'] and perf.get('games', 0) > 10]
            
            if not ratings: return False, 0
            return (self.min_rating <= max(ratings) <= self.max_rating), max(ratings)
        except Exception:
            return False, 0

    def _get_valid_target(self):
        self._refresh_bot_pool()
        now = datetime.now()
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}
        
        # Saniyede 5 profil yerine 3 profile düşürdük (Hız kontrolü)
        tried_this_cycle = 0
        for target in self.bot_pool:
            if tried_this_cycle >= 3: break 
            if target in self.blacklist: continue
            
            tried_this_cycle += 1
            is_suitable, rating = self._check_target_rating(target)
            if is_suitable:
                return target
            else:
                self.blacklist[target] = now + timedelta(hours=24)
        return None

    def start(self):
        if not self.enabled: 
            return

        start_time = time.time()
        while True:
            try:
                # 1. KESİN SLOT KONTROLÜ
                # Eğer 2 maç varsa Matchmaker ASLA ilerlemez.
                if len(self.active_games) >= self.max_parallel_games:
                    time.sleep(45) # Boşuna API sorgusu yapma, 45sn bekle
                    continue

                # 2. GÜVENLİ KAPANIŞ KONTROLÜ
                elapsed = time.time() - start_time
                if elapsed > 20700: # 5s 45dk
                    print("[Matchmaker] Güvenli duruş: Kapanışa az kaldı.")
                    time.sleep(600)
                    continue

                # 3. HEDEF BULMA
                target = self._get_valid_target()
                if not target:
                    time.sleep(60)
                    continue

                # 4. TC SEÇİMİ (Zar mantığı)
                dice = random.random()
                if elapsed > 18000: # 5. saat sonrası sadece hızlı
                    tc_list = ["1+0", "2+1", "3+0"]
                else:
                    if dice < 0.05: tc_list = ["30+0"] # Klasik riskli, 30 yerine 15 yaptık
                    elif dice < 0.20: tc_list = ["10+0", "10+5"]
                    else: tc_list = ["1+0", "2+1", "3+0", "3+2", "5+0", "5+3"]
                
                tc = random.choice(tc_list)
                t_limit, t_inc = map(int, tc.split('+'))

                # 5. SON GÜVENLİK KONTROLÜ
                if len(self.active_games) >= self.max_parallel_games:
                    continue

                # 6. MEYDAN OKUMA VE ZORUNLU BEKLEME
                try:
                    # Rakibi kara listeye al
                    self.blacklist[target] = datetime.now() + timedelta(minutes=90)
                    
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    print(f"📤 [Challenge] -> {target} ({tc}) gönderildi.")
                    
                    # --- EN KRİTİK NOKTA: ABORT KORUMASI ---
                    # Bir meydan okuma attıktan sonra botun "nefes alması" gerekir.
                    # Eğer karşı taraf anında kabul ederse, 2. bir challenge API limitine takılır.
                    # Bu bekleme süresi seni "insan" gibi gösterir.
                    time.sleep(120) # 2 dakika boyunca yeni hiçbir şey yapma.

                except Exception as e:
                    if "429" in str(e): 
                        self._handle_rate_limit(e)
                    else:
                        print(f"⚠️ Challenge reddedildi: {target}")
                        time.sleep(30)

            except Exception as e:
                self._handle_rate_limit(e)
