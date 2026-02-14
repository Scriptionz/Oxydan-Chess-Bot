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
        
        # Elo Sınırları
        self.min_rating = self.config.get("min_rating", 2000)
        self.max_rating = self.config.get("max_rating", 4000)
        self.max_parallel_games = 2 # v4 için eş zamanlı maç sınırı
        
        self.bot_pool = []
        self.blacklist = {}
        self.last_pool_update = 0
        self.pool_timeout = 3600 
        self.consecutive_429s = 0

        self._initialize_id()

    def _initialize_id(self):
        """Hesap bilgilerini güvenli bir şekilde çeker."""
        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Sistem Hazır. ID: {self.my_id} | Hedef: {self.min_rating}-{self.max_rating}")
        except Exception as e:
            print(f"[Matchmaker] Kritik Hata: Kimlik doğrulanamadı. {e}")
            self.my_id = "oxydan" # Yedek

    def _refresh_bot_pool(self):
        """Lichess'ten online bot listesini çeker."""
        now = time.time()
        if not self.bot_pool or (now - self.last_pool_update > self.pool_timeout):
            try:
                print("[Matchmaker] Bot listesi güncelleniyor...", flush=True)
                stream = self.client.bots.get_online_bots()
                online_bots = list(itertools.islice(stream, 100))
                
                self.bot_pool = [b.get('id') for b in online_bots if b.get('id') and b.get('id').lower() != self.my_id.lower()]
                random.shuffle(self.bot_pool)
                self.last_pool_update = now
                self.consecutive_429s = 0 
            except Exception as e:
                print(f"[Matchmaker] Liste çekilemedi: {e}")
                self._handle_rate_limit(e)

    def _handle_rate_limit(self, error):
        """429 Too Many Requests hatasını yönetir."""
        if "429" in str(error):
            self.consecutive_429s += 1
            wait_time = 600 * self.consecutive_429s 
            print(f"!!! [API LIMIT] {wait_time//60} dakika zorunlu uyku modu...")
            time.sleep(wait_time)
        else:
            time.sleep(30)

    def _check_target_rating(self, target_id):
        """Botun profilini inceler ve Elo'sunu kontrol eder."""
        try:
            user_data = self.client.users.get_public_data(target_id)
            if user_data.get('tosViolation') or user_data.get('disabled'):
                return False, 0
                
            perfs = user_data.get('perfs', {})
            ratings = []
            for cat in ['blitz', 'bullet', 'rapid']:
                perf = perfs.get(cat, {})
                if perf.get('games', 0) > 10:
                    ratings.append(perf.get('rating', 0))
            
            if not ratings: return False, 0
            
            max_r = max(ratings)
            return (self.min_rating <= max_r <= self.max_rating), max_r
        except Exception:
            return False, 0

def _get_valid_target(self):
        """Hem Elo hem de kara liste kontrolü yaparak rakip seçer."""
        self._refresh_bot_pool()
        now = datetime.now()
        
        # Süresi dolanları temizle
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}
        
        tried_this_cycle = 0
        for target in self.bot_pool:
            if tried_this_cycle >= 10: break # Daha geniş tarama
            
            if target in self.blacklist:
                continue
            
            tried_this_cycle += 1
            is_suitable, rating = self._check_target_rating(target)
            
            if is_suitable:
                return target
            else:
                # Kriter dışı botları 12 saatliğine listeye ekle ki vakit kaybetmeyelim
                self.blacklist[target] = now + timedelta(hours=12)
        
        return None

def start(self):
        if not self.enabled: 
            print("[Matchmaker] Devre dışı.")
            return

        start_time = time.time()

        while True:
            # STOP.txt kontrolü
            if os.path.exists("STOP.txt"):
                time.sleep(60)
                continue
                
            try:
                # --- SLOT KONTROLÜ (PARALEL MAÇ İÇİN) ---
                if len(self.active_games) >= self.max_parallel_games:
                    time.sleep(15) 
                    continue

                # Uygun rakibi bul
                target = self._get_valid_target()
                if not target:
                    time.sleep(30)
                    continue

                # Zaman kontrolü belirleme
                dice = random.random()
                elapsed = time.time() - start_time
                if elapsed > 18000: 
                    tc_list = ["1+0", "2+1", "3+0"]
                else:
                    if dice < 0.10: tc_list = ["10+0", "5+2"]
                    else: tc_list = ["1+0", "3+0", "3+2", "5+0"]
                
                tc = random.choice(tc_list)
                t_limit, t_inc = map(int, tc.split('+'))

                # Meydan oku
                try:
                    # Aynı bota üst üste gitmemek için kısa süreli blacklist
                    self.blacklist[target] = datetime.now() + timedelta(minutes=45)
                    
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    print(f"[Matchmaker] -> {target} ({tc}) Gönderildi. Slot: {len(self.active_games)}/2")
                    time.sleep(2)
                    
                    # --- AKILLI BEKÇİ (v7 ANTI-ABORT & PARALEL) ---
                    watch_start = time.time()
                    game_found = False
                    
                    while time.time() - watch_start < 40:
                        active_count = len(self.active_games)
                        
                        # DURUM A: Kapasite Doldu (2/2) -> Tam bekleme moduna gir
                        if active_count >= self.max_parallel_games:
                            print(f"[Matchmaker] ✅ Slotlar dolu ({active_count}/2). Maçların bitmesi bekleniyor...")
                            while len(self.active_games) >= self.max_parallel_games:
                                time.sleep(15)
                            print(f"[Matchmaker] 🏁 Bir slot boşaldı. Yeni rakip aranıyor.")
                            game_found = True
                            break
                        
                        # DURUM B: En az 1 maç var ama hala yer var (1/2) -> Hemen aramaya geri dön
                        elif 0 < active_count < self.max_parallel_games:
                            print(f"[Matchmaker] ⚡ Maç başladı. 2. slot için aranıyor...")
                            game_found = True
                            break
                        
                        time.sleep(5)
                    
                    if not game_found:
                        # Teklif kabul edilmediyse veya maç başlamadıysa kısa dinlenme
                        time.sleep(10)

                except Exception as e:
                    # ABORT KORUMASI: Hata veren botu (weiawaga vb.) 3 saatliğine yasakla
                    print(f"[Matchmaker] {target} ile maç kurulamadı veya abort riski: {e}")
                    self.blacklist[target] = datetime.now() + timedelta(hours=3)
                    time.sleep(10)

            except Exception as e:
                self._handle_rate_limit(e)
