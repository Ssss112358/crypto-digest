# Gemini CLI 用 Git Push 手順書（SSH）

## リポジトリ前提

* Repo: `dandandan5/crypto-digest`
* Remote (SSH): `git@github.com:dandandan5/crypto-digest.git`
* Local path（例）: `C:\Users\shrit\Development\crypto\crypto-digest`
* 秘密鍵: `C:\Users\shrit\.ssh\id_ed25519`
* 既に以下は完了済み（事実）

  * `ssh-agent` 常駐化・起動
  * `id_ed25519` を `ssh-add` 済み（指紋: `SHA256:42pOGF...KtA`）
  * `github.com` を `known_hosts` 登録（`ssh -T git@github.com` 成功）
  * `origin` は SSH URL
  * 初回非FFは `--force-with-lease` で解消済み

---

## ①（初回 or 環境復旧時のみ）一度だけ実行する初期化

> 管理者 PowerShell で実行（権限が要るのは最初だけ）

```powershell
# 1) ssh-agent 起動＆常駐
Get-Service ssh-agent | Set-Service -StartupType Automatic
Start-Service ssh-agent
```

> 通常 PowerShell（非管理者）に戻って：

```powershell
# 2) 秘密鍵をエージェントに登録（鍵ファイルがある前提）
ssh-add $env:USERPROFILE\.ssh\id_ed25519

# 3) 初回接続で known_hosts を自動登録
ssh -T git@github.com   # 初回は yes、成功すれば "Hi dandandan5!"
```

※ もし鍵が無い/別名なら作成→GitHubに公開鍵を登録してから `ssh-add`。

---

## ②（毎回の標準フロー）コミット→push

> **リポジトリのルート**で実行

```powershell
cd C:\Users\shrit\Development\crypto\crypto-digest
git status

# 変更があれば
git add -A
git commit -m "feat: <your message>"

# 他者更新を安全に取り込む
git pull --rebase origin main

# プッシュ
git push -u origin main
```

---

## ③（失敗時の分岐・最短解）

### A. `Host key verification failed` / `permission denied`

```powershell
# その場しのぎ（known_hosts 自動登録＋鍵明示）
$env:GIT_SSH_COMMAND = "ssh -i `"$env:USERPROFILE\.ssh\id_ed25519`" -o StrictHostKeyChecking=accept-new"
git push -u origin main

# 恒久対策（推奨）：①の初期化をやり直し（ssh-agent 起動→ssh-add→ssh -T）
```

### B. `Updates were rejected (non-fast-forward)`

リモートに空README初期コミットなどがあるケース。

* 履歴を残したい → **取り込む**：

```powershell
git pull --rebase origin main
git push -u origin main
```

* リモートが“空同然”でローカルを正にしたい → **安全強制**：

```powershell
git push --force-with-lease origin main
```

### C. `error: src refspec HEAD does not match any`

* そこは **Gitリポジトリのディレクトリではない** か、**コミットがない**。
  → ルートへ移動してからコミットを作って再実行。

### D. HTTPS 資格情報と混線した疑い（滅多にないが）

```powershell
git credential-manager erase
rundll32.exe keymgr.dll,KRShowKeyMgr   # GUIで github.com 系を削除
```

---

## ④ Gemini CLI に求める運用ルール（重要）

Gemini は **下記順でだけ** 実行すれば良い。勝手に `ssh-keyscan` や `known_hosts` を直接書き換えない。

1. **場所確認**

   ```powershell
   git rev-parse --is-inside-work-tree
   git remote -v
   ```

   * `origin` は **SSH**：`git@github.com:dandandan5/crypto-digest.git`

2. **直近のpull（rebase）→ push**

   ```powershell
   git pull --rebase origin main
   git push -u origin main
   ```

3. 上記で失敗したら、**A/Bの分岐**に従う

   * ホスト鍵系 → **A**（GIT_SSH_COMMAND か ①初期化）
   * 非FF → **B**（rebase or `--force-with-lease`）

4. どうしてもSSHが不可なら**一時回避**としてのみ HTTPS+PAT を許可

   * ただし**既定は SSH**、設定変更は行わない（URLはSSHのまま）

---

## 付録：一発チェック（Geminiがpush前に必ず走らせる）

```powershell
# 1) 位置とリモート
git rev-parse --is-inside-work-tree
git remote -v

# 2) 鍵がagentに載っているか
ssh-add -l

# 3) GitHubと握手できるか（無言OKなら成功）
ssh -T git@github.com

# 4) 普通に push
git pull --rebase origin main
git push -u origin main
```

---

## ポリシー（意見）

* **標準はSSH**。Geminiは**known_hosts操作を避け**、`ssh -T` での初回承認か `StrictHostKeyChecking=accept-new` に限定。
* **強制pushは `--force-with-lease` だけ**（`--force` は使用禁止）。
* **リモートURLを書き換えない**（常に `git@github.com:dandandan5/crypto-digest.git`）。
* CRLF警告は無視可。気になるなら：

  ```powershell
  git config --global core.autocrlf true
  ```

---

これを `CONTRIBUTING.md` か `scripts/README_PUSH.md` に貼っておけば、Gemini CLIでも迷子にならない。必要なら、上のチェックと分岐をまとめた **PowerShellスクリプト（`scripts/push.ps1`）** も作る。
