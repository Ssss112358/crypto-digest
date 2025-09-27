# scripts/push.ps1

# 1) 位置とリモートの確認
git rev-parse --is-inside-work-tree
if ($LASTEXITCODE -ne 0) {
    Write-Error "Not inside a Git work tree. Please navigate to the repository root."
    exit 1
}
git remote -v

# 2) 鍵がagentに載っているか
ssh-add -l
if ($LASTEXITCODE -ne 0) {
    Write-Warning "SSH agent might not be running or key not added. Please ensure SSH agent is running and key is added."
    # ここでssh-agentの起動やssh-addを試みることも可能だが、初回初期化に任せる
}

# 3) GitHubと握手できるか（無言OKなら成功）
ssh -T git@github.com
if ($LASTEXITCODE -ne 0) {
    Write-Warning "SSH connection to GitHub failed. Push might fail. Consider re-running initial setup steps."
    # ここでAの分岐（GIT_SSH_COMMAND）を試すことも可能だが、まずは標準フローで
}

# 4) 普通に push
git pull --rebase origin main
if ($LASTEXITCODE -ne 0) {
    Write-Error "git pull --rebase failed. Please resolve conflicts or check remote. If 'Updates were rejected', consider 'git pull --rebase origin main' followed by 'git push -u origin main' or 'git push --force-with-lease origin main'."
    exit 1
}
git push -u origin main
if ($LASTEXITCODE -ne 0) {
    Write-Error "git push failed. Check the error message for details (e.g., Host key verification failed, permission denied, non-fast-forward). Refer to the Git Push Manual for troubleshooting."
    exit 1
}

Write-Host "Git push completed successfully."
