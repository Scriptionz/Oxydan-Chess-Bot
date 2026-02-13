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
        self.active_games = active_games  # Ana koddaki set'e referans
        self.my_id = None
        
        # Elo Sınırları
        self.min_rating = self.config.get("min_rating", 2000)
        self.max_rating = self.config.get("max_rating", 4000)
        self.max_parallel_games = 3 # v4 için eş zamanlı maç sınırı
        
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
        # Liste boşsa veya süre dolduysa güncelle
        if not self.bot_pool or (now - self.last_pool_update > self.pool_timeout):
            try:
                print("[Matchmaker] Bot listesi güncelleniyor...", flush=True)
                # get_online_bots() generator döndürür, islice ile sınırlıyoruz
                stream = self.client.bots.get_online_bots()
                online_bots = list(itertools.islice(stream, 100))
                
                # Kendimizi ve pasifleri ayıklayarak listeyi oluştur
                self.bot_pool = [b.get('id') for b in online_bots if b.get('id') and b.get('id').lower() != self.my_id.lower()]
                random.shuffle(self.bot_pool)
                self.last_pool_update = now
                self.consecutive_429s = 0 # Başarılı istekte hata sayacını sıfırla
            except Exception as e:
                print(f"[Matchmaker] Liste çekilemedi: {e}")
                self._handle_rate_limit(e)

    def _handle_rate_limit(self, error):
        """429 Too Many Requests hatasını yönetir."""
        if "429" in str(error):
            self.consecutive_429s += 1
            wait_time = 600 * self.consecutive_429s # 10, 20, 30... dakika bekle
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
            # Sadece aktif olduğu kategorilere bak (oyun sayısı > 10 olanlar)
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
        
        # Süresi dolan yasakları temizle
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}
        
        # API'yi yormamak için her döngüde en fazla 5 profili sorgula
        tried_this_cycle = 0
        for target in self.bot_pool:
            if tried_this_cycle >= 5: break
            
            if target in self.blacklist:
                continue
            
            tried_this_cycle += 1
            is_suitable, rating = self._check_target_rating(target)
            
            if is_suitable:
                return target
            else:
                # Kriter dışı botu 24 saat boyunca bir daha sorma
                self.blacklist[target] = now + timedelta(hours=24)
        
        return None

    def start(self):
        if not self.enabled: 
            print("[Matchmaker] Devre dışı.")
            return

        while True:
            try:
                # 1. DEĞİŞİKLİK: Yerel slot kontrolü (API'yi yormaz)
                # Eğer 3 maç da doluysa yeni maç arama, bekle.
                if len(self.active_games) >= self.max_parallel_games:
                    time.sleep(30) 
                    continue

                # 2. Hedef bul
                target = self._get_valid_target()
                if not target:
                    print("[Matchmaker] Uygun rakip bulunamadı, bekleniyor...")
                    time.sleep(120)
                    continue

                # 3. Zaman kontrolü
                tcs = self.config.get("time_controls", ["3+0", "3+2", "5+0"])
                tc = random.choice(tcs)
                t_limit, t_inc = map(int, tc.split('+'))

                # 4. Meydan oku
                try:
                    # Spam engeli için hemen listeye al
                    self.blacklist[target] = datetime.now() + timedelta(minutes=120)
                    
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    print(f"[Matchmaker] -> {target} ({tc}) Meydan okuma gönderildi.")
                    
                    # 3. DEĞİŞİKLİK: Maç arama aralığını kısalttık
                    # Artık 3 slotumuz olduğu için bir maç gönderdikten sonra 
                    # diğer slotları doldurmak için çok uzun beklemeye gerek yok.
                    time.sleep(random.randint(20, 45))

                except Exception as e:
                    if "429" in str(e): 
                        self._handle_rate_limit(e)
                    else:
                        print(f"[Matchmaker] Meydan okuma hatası: {target}")
                        self.blacklist[target] = datetime.now() + timedelta(hours=6)
                        time.sleep(10)

            except Exception as e:
                self._handle_rate_limit(e)
