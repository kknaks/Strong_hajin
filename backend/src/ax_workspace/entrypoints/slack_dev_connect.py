"""**개발 전용** — `~/.slack_test_token` 을 회원의 「연결된 슬랙 연동」으로 넣는다(4차 검수 ★3 · `make slack-dev-connect`).

운영 프로파일에서는 거절한다. 토큰은 표준 입력으로만 받고 어디에도 찍지 않는다.
"""
import argparse
import sys

from ax_workspace.bootstrap.external_worker import connect_dev_slack
from ax_workspace.bootstrap.settings import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--member", required=True, help="연동을 가질 회원 id (예: mina)")
    args = parser.parse_args(argv)
    result = connect_dev_slack(Settings.from_environment(), args.member, sys.stdin.read())
    print(f"slack development integration ready: {result['integration_id']} ({result['team']}) for {args.member}")


if __name__ == "__main__":
    main()
