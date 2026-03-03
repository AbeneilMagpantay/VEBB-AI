# ☁️ How to Run VEBB-AI on the Cloud for FREE
**Using Google Cloud Platform (GCP) Free Tier**

## Step 1: Get Your Free Server
1. Go to [Google Cloud Console](https://console.cloud.google.com/) and sign in.
2. Enable billing (required for identity verification, even for free tier).
3. Go to **Compute Engine** > **VM Instances**.
4. Click **Create Instance**.
5. **Name**: `vebb-ai-bot`
6. **Region**: Choose `asia-east1` (Taiwan).
   > **⚠️ IMPORTANT**: While US regions are free, they are banned by Binance. Taiwan is safe but **NOT** in the Free Tier (costs ~$7/month). You must use a non-US region!
7. **Machine Type**: Choose **e2-micro** (2 vCPUs, 1GB RAM).
8. **Boot Disk**: Change to **Ubuntu 22.04 LTS**.
9. **Firewall**: Check "Allow HTTP/HTTPS traffic".
10. Click **Create**.

---

## Step 2: Connect via Browser
1. In the VM Instances list, find `vebb-ai-bot`.
2. Click the **SSH** button next to it.
3. A browser window will open with a terminal connected to your server.

---

## Step 3: Upload Your Files
Since your code is not on GitHub, you'll upload it directly:

1. In the SSH browser window, click the **Gear Icon (⚙️)** in the top-right corner.
2. Select **Upload File**.
3. Select ALL your project files from your `VEBB-AI` folder:
   - `main.py`, `exchange_client.py`, `gemini_analyst.py`, `...` (all .py files)
   - `requirements.txt`
   - `.env`
   - `deploy.sh`
   - `hmm_model.pkl` and `hmm_model_5m.pkl`
4. Click **Upload** (this might take a minute).

---

## Step 4: Install & Run
Once files are uploaded, run these commands in the SSH terminal:

```bash
# Make script executable
chmod +x deploy.sh

# Run setup
./deploy.sh
```

---

## Step 5: Start 24/7
Run these commands to start the bot in a background session:
```bash
tmux new -s vebb         # Create session
./start_bot.sh           # Start bot
```

**To leave it running:** Press `Ctrl+B`, then press `D`.
**To check on it later:** `tmux attach -t vebb`

---

## ️ Monitoring & Maintenance

### 1. Check if it's running
Reconnect to your server (SSH) and run:
```bash
tmux attach -t vebb
```
Ideally, you should see the bot's colored output updating with price data.
To detach again (leave it running), press `Ctrl+B`, then `D`.

### 2. Check processes
If `tmux` is closed, check if python is running:
```bash
ps aux | grep python
```

### 3. Check logs
The bot saves logs to the `logs/` directory. View the latest errors or trades:
```bash
tail -f logs/app.log   # Build this log file tracking if not present
ls -l logs/trades/     # See trade history
```

### 4. Stop the bot
Inside the tmux session, press `Ctrl+C`.
To kill the session entirely:
```bash
tmux kill-session -t vebb
```
