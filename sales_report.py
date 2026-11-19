"""
매출 데이터 자동 집계 스크립트 (Python + SQL, 표준 라이브러리만 사용)

동작:
  1. inbox/ 폴더의 CSV 파일을 읽어 SQLite DB(sales.db)에 적재 (중복 주문은 무시)
  2. SQL로 일별 매출 / 상품별 순위 / 지역별 매출을 집계
  3. reports/ 폴더에 CSV 리포트 저장 (엑셀에서 바로 열림)
  4. 처리한 원본 파일은 archive/ 로 이동

사용법:
  python sales_report.py            # 한 번 실행
  python sales_report.py --demo     # 샘플 데이터 생성 후 실행
  cron 등록 예: 0 9 * * * cd /경로 && python3 sales_report.py

입력 CSV 형식 (헤더 필수):
  order_id,order_date,product,region,quantity,unit_price
"""

import argparse
import csv
import logging
import random
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
INBOX, ARCHIVE, REPORTS = BASE / "inbox", BASE / "archive", BASE / "reports"
DB_PATH = BASE / "sales.db"
REQUIRED = ["order_id", "order_date", "product", "region", "quantity", "unit_price"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(BASE / "run.log", encoding="utf-8")],
)
log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    order_id    TEXT PRIMARY KEY,
    order_date  TEXT NOT NULL,
    product     TEXT NOT NULL,
    region      TEXT NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  REAL NOT NULL CHECK (unit_price >= 0),
    source_file TEXT,
    loaded_at   TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(order_date);
"""

# 리포트 이름 -> SQL
REPORT_QUERIES = {
    "daily_sales": """
        SELECT order_date                       AS 날짜,
               COUNT(*)                         AS 주문수,
               SUM(quantity)                    AS 판매수량,
               CAST(ROUND(SUM(quantity*unit_price)) AS INTEGER)  AS 매출액
        FROM sales
        GROUP BY order_date
        ORDER BY order_date DESC
    """,
    "product_ranking": """
        SELECT RANK() OVER (ORDER BY SUM(quantity*unit_price) DESC) AS 순위,
               product                          AS 상품,
               SUM(quantity)                    AS 판매수량,
               CAST(ROUND(SUM(quantity*unit_price)) AS INTEGER)  AS 매출액,
               ROUND(100.0 * SUM(quantity*unit_price)
                     / (SELECT SUM(quantity*unit_price) FROM sales), 1) AS 매출비중_pct
        FROM sales
        GROUP BY product
        ORDER BY 순위
    """,
    "region_monthly": """
        SELECT substr(order_date, 1, 7)         AS 월,
               region                           AS 지역,
               CAST(ROUND(SUM(quantity*unit_price)) AS INTEGER)  AS 매출액,
               CAST(ROUND(AVG(quantity*unit_price)) AS INTEGER)  AS 평균주문금액
        FROM sales
        GROUP BY 월, region
        ORDER BY 월 DESC, 매출액 DESC
    """,
}


def load_csv(conn: sqlite3.Connection, path: Path) -> tuple[int, int, int]:
    """CSV 한 개를 적재. (신규, 중복, 오류) 건수 반환."""
    inserted = dup = bad = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = set(REQUIRED) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"필수 컬럼 누락: {sorted(missing)}")
        for line_no, row in enumerate(reader, start=2):
            try:
                values = (
                    row["order_id"].strip(),
                    date.fromisoformat(row["order_date"].strip()).isoformat(),
                    row["product"].strip(),
                    row["region"].strip(),
                    int(row["quantity"]),
                    float(row["unit_price"]),
                    path.name,
                )
                cur = conn.execute(
                    "INSERT OR IGNORE INTO sales "
                    "(order_id, order_date, product, region, quantity, unit_price, source_file) "
                    "VALUES (?,?,?,?,?,?,?)",
                    values,
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    dup += 1
            except (ValueError, KeyError, sqlite3.IntegrityError) as e:
                bad += 1
                log.warning("%s %d행 건너뜀: %s", path.name, line_no, e)
    return inserted, dup, bad


def export_reports(conn: sqlite3.Connection) -> None:
    stamp = date.today().isoformat()
    for name, sql in REPORT_QUERIES.items():
        cur = conn.execute(sql)
        out = REPORTS / f"{stamp}_{name}.csv"
        with out.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: 엑셀 한글 깨짐 방지
            w = csv.writer(f)
            w.writerow([c[0] for c in cur.description])
            w.writerows(cur.fetchall())
        log.info("리포트 저장: %s", out.relative_to(BASE))


def make_demo_data(n_files: int = 2, rows: int = 50) -> None:
    products = {"노트북": 1_200_000, "모니터": 300_000, "키보드": 80_000, "마우스": 35_000, "헤드셋": 120_000}
    regions = ["서울", "부산", "대구", "인천", "광주"]
    oid = 1000
    for i in range(n_files):
        path = INBOX / f"sales_demo_{i + 1}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(REQUIRED)
            for _ in range(rows):
                oid += 1
                p = random.choice(list(products))
                d = date.today() - timedelta(days=random.randint(0, 40))
                w.writerow([f"ORD-{oid}", d, p, random.choice(regions), random.randint(1, 5), products[p]])
            w.writerow([f"ORD-{oid}", d, p, "서울", 1, products[p]])  # 중복 주문 예시
            w.writerow(["ORD-BAD", "2026-13-45", "모니터", "서울", "x", 0])  # 잘못된 행 예시
        log.info("샘플 생성: %s", path.relative_to(BASE))


def main() -> None:
    parser = argparse.ArgumentParser(description="매출 CSV 적재 및 리포트 자동화")
    parser.add_argument("--demo", action="store_true", help="샘플 CSV를 만든 뒤 실행")
    args = parser.parse_args()

    for d in (INBOX, ARCHIVE, REPORTS):
        d.mkdir(exist_ok=True)
    if args.demo:
        make_demo_data()

    files = sorted(INBOX.glob("*.csv"))
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        for path in files:
            try:
                ins, dup, bad = load_csv(conn, path)
                conn.commit()
                shutil.move(str(path), ARCHIVE / path.name)
                log.info("%s: 신규 %d / 중복 %d / 오류 %d", path.name, ins, dup, bad)
            except Exception as e:
                conn.rollback()
                log.error("%s 처리 실패 (inbox에 그대로 둠): %s", path.name, e)

        total = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        if total == 0:
            log.info("DB에 데이터가 없어 리포트를 건너뜁니다. inbox/에 CSV를 넣어주세요.")
            return
        export_reports(conn)
        log.info("완료: 새 파일 %d개 처리, DB 누적 %d건", len(files), total)


if __name__ == "__main__":
    main()
