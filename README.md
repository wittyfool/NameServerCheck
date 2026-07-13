# NameServerCheck

DNS ゾーンファイルに記載された各レコードセットを指定ネームサーバーへ問い合わせ、値の不足・過剰を比較する CLI です。TTL は比較対象に含みません。

## セットアップ

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 使用方法

```powershell
python nameserver_check.py <zone-file> <nameserver> [options]
```

例:

```powershell
python nameserver_check.py example.com.zone 192.0.2.53 --progress
python nameserver_check.py example.com.zone 192.0.2.53 --progress 10
python nameserver_check.py example.com.zone ns1.example.com --type A --type AAAA
python nameserver_check.py example.com.zone ns1.example.com --nameserver-family ipv4
python nameserver_check.py example.com.zone 192.0.2.53 --type A,MX --progress
python nameserver_check.py example.com.zone 192.0.2.53 --type A --interval 1
```

主なオプション:

- `--progress [N]`: N件完了ごと、および全件完了時に進捗を表示（N省略時は20件）
- `--type TYPE`: 比較対象タイプを指定。複数回またはカンマ区切りで指定可能
- `--nameserver-family {auto,ipv4,ipv6}`: 問い合わせ先ネームサーバーのアドレス種別を指定
- `--timeout SEC`: DNS 問い合わせのタイムアウト秒（既定値 5）
- `--interval SEC`: 問い合わせ間隔の秒数（既定値 0、小数指定可）
- `--origin NAME`: `$ORIGIN` がないゾーンファイル用のオリジン

ネームサーバーにホスト名を指定した場合は、解決できた複数アドレスを順に試行します。到達不能なアドレスが先に返る Linux 環境では、`--nameserver-family ipv4` の指定や IPv4 アドレスの直接指定が切り分けに有効です。

差分は `-`（ゾーンファイルにのみ存在）と `+`（ネームサーバーにのみ存在）で表示します。問い合わせエラー時は試行したネームサーバーアドレスごとの失敗内容を表示します。終了コードは、一致なら `0`、差分または問い合わせエラーなら `1`、引数・ファイル等のエラーなら `2` です。
