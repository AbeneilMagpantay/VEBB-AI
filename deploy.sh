#!/bin/bash

# VEBB-AI Deployment Script
# Run this on your fresh Ubuntu server to set up the bot

echo "🚀 Starting VEBB-AI Setup..."

# 1. Update System
echo "📦 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# 2. Install Python 3.10+ and Pip
echo "🐍 Installing Python..."
sudo apt install -y python3 python3-pip python3-venv git tmux

# 3. Clone Repository (if not already done)
# If you uploaded files manually, skip this.
# git clone https://github.com/yourusername/VEBB-AI.git
# cd VEBB-AI

# 4. Create Virtual Environment
echo "🔧 Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# 5. Install Dependencies
echo "📚 Installing dependencies..."
pip install -r requirements.txt
pip install pandas numpy requests python-dotenv ta-lib

# 6. Create .env files if missing
if [ ! -f .env ]; then
    echo "⚠️ .env file missing! Creating template..."
    cp .env.example .env
    echo "Please edit .env with your keys!"
fi

# 7. Create Startup Script
echo "📝 Creating startup script..."
cat << 'EOF' > start_bot.sh
#!/bin/bash
source venv/bin/activate
python3 run_dual.py
EOF
chmod +x start_bot.sh

echo "✅ Setup Complete!"
echo ""
echo "To start the bot 24/7:"
echo "1. Edit your config:  nano .env"
echo "2. Start session:     tmux new -s vebb"
echo "3. Run bot:           ./start_bot.sh"
echo "4. Detach:            Ctrl+B, then D"
