# 매출 CSV 자동 집계

`inbox/`에 넣은 매출 CSV를 SQLite(`sales.db`)에 적재하고, SQL로 집계한 리포트를 `reports/`에 CSV로 저장합니다. Python 3.9+ 표준 라이브러리만 사용합니다.

## 실행

```bash
python3 sales_report.py --demo   # 샘플 데이터로 체험
python3 sales_report.py          # inbox/의 CSV 처리
```

CSV 헤더: `order_id,order_date,product,region,quantity,unit_price`

## 리포트
- `daily_sales`: 날짜별 주문 수 / 판매 수량 / 매출액
- `product_ranking`: 상품별 매출 순위·비중
- `region_monthly`: 월별·지역별 매출과 평균 주문 금액

리포트를 추가하려면 `REPORT_QUERIES`에 SQL을 넣으면 됩니다.

## 매일 자동 실행 (Linux/macOS cron)
```
0 9 * * * cd /경로/sales_automation && python3 sales_report.py
```
