# garmin-mcp-community

Unofficial, community-maintained MCP server for Garmin Connect.

Garmin Connect の健康・トレーニングデータ（睡眠、ストレス、Body Battery、ステップ数、アクティビティなど）を、AIエージェントが MCP（Model Context Protocol）経由で参照できるようにするサーバーです。

> **注意**: 本プロジェクトは Garmin 社の公式製品ではありません。個人向け公式 API が提供されていないため、非公式ライブラリ [garminconnect](https://pypi.org/project/garminconnect/) を利用します。Garmin 側の仕様変更により動作しなくなる可能性があります。また、本サーバーが返す情報は体調管理の参考情報であり、医療上の診断を行うものではありません。

## 設計方針

- **Cloud Run 上の Web サービス**として運用（Streamable HTTP、ステートレス）
- **サーバーはセッション情報・健康データを保持しない**。認証は認証画面（`/auth`）で利用者本人が行い、発行されたトークンはエージェント（クライアント）側が保持する。MCP 呼び出し時に `Authorization: Bearer <token>` ヘッダーで渡し、サーバーはリクエスト内でのみ使用する
- **不特定多数が使える**マルチユーザー前提（ユーザー登録・ユーザー DB を持たない）
- v1 は**読み取り専用**。トレーニング計画は AI 側が持ち、本サーバーは実績と体調を返す

## ツール（v1）

| ツール | 内容 |
|---|---|
| `get_user_profile` | 表示名とタイムゾーン |
| `get_daily_summary` | ステップ数、カロリー、運動量、安静時心拍の日次要約 |
| `get_sleep` | 睡眠時間、スコア、ステージ内訳 |
| `get_stress` | ストレスレベルの要約 |
| `get_body_battery` | Body Battery の推移と要約 |
| `get_heart_rate` | 安静時心拍と推移の要約 |
| `get_activities` | アクティビティ一覧 |
| `get_activity_detail` | アクティビティ詳細 |
| `get_trends` | 期間平均と傾向（最大 90 日） |
| `get_recovery_status` | 睡眠・Body Battery・ストレス・安静時心拍の合成コンディション |

詳細は [docs/SPEC.md](docs/SPEC.md) を参照してください。

## ステータス

仕様 v1 を策定済み。実装中です。

## License

MIT
