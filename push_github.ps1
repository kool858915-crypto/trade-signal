# GitHub に push するスクリプト (初回のみ gh auth login が必要)
$env:GIT_CONFIG_COUNT = 1
$env:GIT_CONFIG_KEY_0 = 'safe.directory'
$env:GIT_CONFIG_VALUE_0 = 'E:/投資'

Set-Location $PSScriptRoot

gh auth status 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "GitHub にログインしてください:"
    gh auth login --web --git-protocol https
}

$exists = gh repo view trade-signal 2>$null
if ($LASTEXITCODE -ne 0) {
    gh repo create trade-signal --public --source=. --remote=origin --push
} else {
    git remote remove origin 2>$null
    $user = gh api user -q .login
    git remote add origin "https://github.com/$user/trade-signal.git"
    git push -u origin main
}

Write-Host ""
Write-Host "完了! リポジトリURL:"
gh repo view --web 2>$null
gh repo view --json url -q .url
