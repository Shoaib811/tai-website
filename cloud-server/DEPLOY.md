# 🚀 TAI CLOUD — VPS Deployment Guide

> Ye guide follow karke tumhara localhost server 30-45 minute mein REAL internet
> pe live ho jayega. Har command copy-paste ready hai.

---

## 💰 STEP 0: Kya Khareedna Hai (Shopping List)

| Item | Kahan Se | Cost | Note |
|------|----------|------|------|
| VPS (Ubuntu 24.04) | hetzner.com → CX22 | ~€4/mahina (~₹400) | DigitalOcean/Contabo bhi chalega |
| Domain | namecheap.com / godaddy.com | ~₹800/saal | e.g. `taicrypt.app`, `taicloud.xyz` |

VPS banate waqt choose karo: **Ubuntu 24.04 LTS**, location **Europe ya India**
(jo user base ke paas ho), aur root password / SSH key save kar lo.

---

## 🌐 STEP 1: Domain Ko VPS Se Jodo (DNS)

1. Domain provider ke dashboard mein jao (Namecheap → Advanced DNS)
2. **A Record** add karo:
   - Host: `api` (to URL banega `api.tumhara-domain.com`)
   - Value: `<tumhare VPS ka IP>`
   - TTL: 300 (5 min)
3. Check karo 10-15 min baad (apne PC pe PowerShell):
   ```powershell
   nslookup api.tumhara-domain.com
   ```
   Agar VPS IP wapas aaye → DNS ready ✓

---

## 🖥️ STEP 2: VPS Pe Pehli Baar Login

Apne PC se PowerShell kholo:
```bash
ssh root@TUMHARA_VPS_IP
```

Pehli baar update + zaroori packages:
```bash
apt update && apt upgrade -y
apt install -y python3-venv python3-pip caddy ufw rsync
```

Firewall (sirf SSH + web khula, kuch aur nahi):
```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```
> ⚠️ Port 8800 ko PUBLIC kabhi mat kholna — sirf Caddy andar se baat karega!

Server user banao (root ke naam se nahi chalega app):
```bash
adduser --disabled-password --gecos "" tai
mkdir -p /opt/taicloud/data
chown -R tai:tai /opt/taicloud
```

---

## 📤 STEP 3: Code Upload Karo (Ye APNE PC Pe Chalana)

Naye PowerShell window mein:
```powershell
scp "E:\tricrypt AI\cloud-server\app.py" root@TUMHARA_VPS_IP:/opt/taicloud/app.py
scp "E:\tricrypt AI\cloud-server\requirements.txt" root@TUMHARA_VPS_IP:/opt/taicloud/requirements.txt
scp "E:\tricrypt AI\cloud-server\deploy\Caddyfile" root@TUMHARA_VPS_IP:/etc/caddy/Caddyfile
scp "E:\tricrypt AI\cloud-server\deploy\taicloud.service" root@TUMHARA_VPS_IP:/etc/systemd/system/taicloud.service
```

**IMPORTANT:** Ab VPS pe Caddyfile edit karo (domain replace):
```bash
nano /etc/caddy/Caddyfile
# line badlo:  api.taicrypt.example  →  api.TUMHARA-DOMAIN.com
# save: Ctrl+O, Enter, Ctrl+X
```

---

## ⚙️ STEP 4: Server Setup + Start (VPS Pe)

```bash
cd /opt/taicloud
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
chown -R tai:tai /opt/taicloud

systemctl daemon-reload
systemctl enable --now taicloud.service
systemctl status taicloud.service        # "active (running)" dikhna chahiye
systemctl restart caddy                   # HTTPS certificate auto le lega
```

Logs dekhne ho to:
```bash
journalctl -u taicloud -f
```

## ✅ STEP 5: LIVE TEST

Kisi bhi browser mein:
```
https://api.TUMHARA-DOMAIN.com/healthz
→ {"ok": true, ...} dikhna chahiye 🔒 (https + taala icon = SSL chal raha!)

https://api.TUMHARA-DOMAIN.com/docs
→ Poora interactive API documentation!
```

---

## 🔄 STEP 6: Apne Client Ko Real Server Pe Switch Karo

Apne PC pe PowerShell:
```powershell
cd "E:\tricrypt AI\tai"
python -c "import taicloud; taicloud.set_server('https://api.TUMHARA-DOMAIN.com/api/v1')"
```

Phir TAI app kholo:
1. Purane account se logout karo (email button → logout)
2. Dobara login/signup karo (naya server hai, naya account banega)
3. File load karo → ab wo INTERNET wale server pe jayegi! 🌍

> Wapas localhost pe jaana ho:
> ```powershell
> python -c "import taicloud; taicloud.set_server('http://127.0.0.1:8800/api/v1')"
> ```

---

## 🔐 STEP 7: Production Security Notes

| Baat | Kyun Zaroori |
|------|--------------|
| `server_config.json` ka BACKUP rakho (`/opt/taicloud/server_config.json`) | Isme JWT secret hai — kho gaya to sab users logout |
| `/opt/taicloud/data` ka weekly backup | Ye tumhara poora business data hai! Cron: `rsync -a /opt/taicloud/data /backup/` |
| Root login band karo (baad mein) | `passwd -l root` jab apna sudo user ban jao |
| `apt unattended-upgrade` on rakho | Auto security patches |
| Users 500+ hone pe | SQLite → PostgreSQL migration (M-plan mein already likha hai) |

---

## 🧯 Common Problems (Troubleshooting)

| Problem | Fix |
|---------|-----|
| `healthz` nahi chal raha | `systemctl status taicloud` + `journalctl -u taicloud -n 50` dekho |
| SSL certificate nahi mila | DNS propagate hona baaki hai (15 min wait) — `nslookup` se confirm |
| 502 Bad Gateway | App down hai: `systemctl restart taicloud` |
| Upload fail 413 | Caddyfile ka `max_size 1MB` check karo |
| Client "network_error" deta hai | `set_server` URL sahi likha? `/api/v1` ending zaroori hai |

---

## ✔️ Deploy Ho Gaya? Milestone M4 COMPLETE!

Uske baad:
- **M5:** Website (landing page + signup flow) + Razorpay/LemonSqueezy payments
- **M6:** 5-10 doston ko free beta accounts → feedback → PUBLIC LAUNCH 🎉
