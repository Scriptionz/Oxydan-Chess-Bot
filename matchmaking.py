import time
import random

class Matchmaker:
    def __init__(self, client, config):
        self.client = client
        self.config = config.get("matchmaking", {})
        self.enabled = self.config.get("allow_feed", False)

    def start(self):
        print(f"[Matchmaker] Avcı modu devrede...")
        
        while True:
            try:
                # 1. Oyun kontrolü
                ongoing = self.client.account.get_ongoing()
                if len(ongoing) >= self.config.get("max_games", 1):
                    time.sleep(30)
                    continue

                print("[Matchmaker] Rakipler taranıyor...")
                
                # 2. BOTLARI BULMA (DOĞRU YÖNTEM)
                # get_online_bots() bir yayındır. İlk 50 botu hızlıca çekip listeye alıyoruz.
                online_bots_stream = self.client.bots.get_online_bots()
                
                # Yayından sadece ilk 50 botu al ve listeye çevir
                online_bots = list(itertools.islice(online_bots_stream, 50))
                
                target_bots = []
                for bot in online_bots:
                    # 'id' bazen sözlük içinde olmayabilir, güvenli alalım
                    b_id = bot.get('id') or bot.get('username')
                    
                    if b_id and b_id.lower() != self.my_id.lower():
                        target_bots.append(b_id)

                if target_bots:
                    # Rastgele bir kurban seç
                    target = random.choice(target_bots)
                    
                    time_control = random.choice(self.config.get("time_controls", ["3+2"]))
                    t_limit, t_inc = map(int, time_control.split('+'))
                    
                    print(f"[Matchmaker] Hedef yakalandı: {target}. Saldırı başlatılıyor...")
                    
                    self.client.challenges.create(
                        target, 
                        rated=True, 
                        clock_limit=t_limit * 60, 
                        clock_increment=t_inc
                    )
                else:
                    print("[Matchmaker] Uygun hedef bulunamadı, tekrar denenecek.")

                time.sleep(self.config.get("challenge_interval", 60))

            except Exception as e:
                print(f"[Matchmaker] Hata: {e}")
                time.sleep(60)
