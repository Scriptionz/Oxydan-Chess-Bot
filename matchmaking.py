import time
import random
import itertools

class Matchmaker:
    def __init__(self, client, config):
        self.client = client
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", True)
        
        # Kendi ID'mizi doğru alalım
        try:
            self.my_id = self.client.account.get()['id']
            print(f"[Matchmaker] Kimlik: {self.my_id}")
        except:
            self.my_id = "oxydan"

    def start(self):
        print(f"[Matchmaker] Avcı modu aktif. Durum: {self.enabled}")
        
        if not self.enabled:
            return

        while True:
            try:
                # DÜZELTİLEN SATIR: account yerine games kullanıyoruz
                ongoing = self.client.games.get_ongoing()
                
                max_games = self.config.get("max_games", 1)
                if len(ongoing) >= max_games:
                    #print("[Matchmaker] Şu an maçta, beklemede...")
                    time.sleep(30)
                    continue

                # Rakipleri tara
                print("[Matchmaker] Rakip aranıyor...")
                online_bots_stream = self.client.bots.get_online_bots()
                online_bots = list(itertools.islice(online_bots_stream, 50))
                
                target_bots = [b.get('id') for b in online_bots if b.get('id') and b.get('id').lower() != self.my_id.lower()]

                if target_bots:
                    target = random.choice(target_bots)
                    
                    # Zaman ayarı
                    tc = random.choice(self.config.get("time_controls", ["3+2"]))
                    t_limit, t_inc = map(int, tc.split('+'))
                    
                    print(f"[Matchmaker] Hedef seçildi: {target}. Meydan okunuyor...")
                    
                    # Meydan okuma gönder
                    self.client.challenges.create(
                        username=target,
                        rated=True,
                        clock_limit=t_limit * 60,
                        clock_increment=t_inc
                    )
                    print(f"[Matchmaker] İstek {target} botuna gönderildi.")
                else:
                    print("[Matchmaker] Uygun rakip bulunamadı.")

                # Bekleme süresi
                interval = self.config.get("challenge_interval", 60)
                time.sleep(interval)

            except Exception as e:
                print(f"[Matchmaker] Hata yakalandı: {e}")
                time.sleep(60)
