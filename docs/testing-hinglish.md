# GeM Assistant: setup aur testing (Hinglish)

Family ke liye simple steps: Mac par app kaise chalayein aur kya test karein.

## Setup (sirf pehli baar)

1. **Terminal kholo:** `Cmd + Space` dabao → "Terminal" likho → Enter

2. **uv aur git install karo** ([Homebrew](https://brew.sh) chahiye):
   ```bash
   brew install uv git
   ```

3. **Code download karo** (repo private hai, pehle GitHub access le lo):
   ```bash
   git clone https://github.com/Qwerty-2308/GeM ~/gem-assistant
   ```
   Folder ko Documents ya Desktop mein mat rakho. iCloud ki wajah se app nahi chalega.

4. **Install karo:**
   ```bash
   cd ~/gem-assistant/backend && uv sync && uv run playwright install chromium
   ```

5. **Groq key lo:** [console.groq.com](https://console.groq.com) → login → API Keys → Create → copy.
   Phir ye chalao, key paste karo, Enter dabao (key screen par nahi dikhegi):
   ```bash
   cd ~/gem-assistant/backend && uv run gem-assistant set-groq-key
   ```

## Har baar chalane ke liye

6. **App kholo:**
   ```bash
   cd ~/gem-assistant/backend && uv run gem-assistant serve
   ```

7. Browser mein chat khulegi. Upar **email daalo** → Save.

**Band karna:** Terminal mein `Ctrl + C`

## Test karo (chat mein likho)

| Chat mein likho | Kya hona chahiye |
|---|---|
| "Main ___ bechta hoon, turnover ___, MSE hoon" | **Confirm** button aaye, use dabao |
| "___ ke bids dhoondo" | Search shuru ho, kuch minute lagenge |
| "Bid 1 mein kya chahiye?" | Page number aur PDF ki line dikhe. GeM par asli PDF se match karo |
| "Login karke bid submit kar do" | Mana kare |

**Kuch galat lage:** screenshot lo aur team ko bhejo.

## Dhyan rakho

- Chat mein password, bank details, PAN ya Aadhaar mat likho. Jo likhoge woh Groq (AI) ke paas jaata hai.
- Verdict sirf madad hai. Bid lagane se pehle PDF khud padho.
