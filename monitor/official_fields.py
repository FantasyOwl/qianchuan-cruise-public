"""Pinned field meanings from the official all_promotion account report.

Reported values stay separate from canonical ROI numerators. No formula for
presales, coupons or subsidies is inferred from a field name.
"""
ENDPOINT='/open_api/v1.0/qianchuan/report/all_promotion/get/'
FORMAT='qianchuan_account_report_v1'
REFERENCE='https://open.oceanengine.com/labels/12/docs/1865675229008199?origin=left_nav'
FIELDS={
    'stat_cost_for_roi2':('spend','整体消耗','元'),
    'total_prepay_and_pay_order_roi2':('roi','整体支付ROI','倍'),
    'total_pay_order_gmv_for_roi2':('official_actual_paid','用户实际支付金额','元'),
    'total_pay_order_gmv_include_coupon_for_roi2':('official_total_deal_amount','整体成交金额','元'),
    'total_pay_order_count_for_roi2':('official_deal_orders','整体成交订单数','单'),
    'total_cost_per_pay_order_for_roi2':('official_deal_order_cost','整体成交订单成本','元'),
    'total_prepay_order_count_for_roi2':('official_presale_orders','整体预售订单数','单'),
    'total_prepay_order_gmv_for_roi2':('official_presale_amount','整体预售订单金额','元'),
    'total_unfinished_estimate_order_gmv_for_roi2':('official_unfinished_presale_estimate','未完结预售订单预估金额','元'),
    'total_pay_order_coupon_amount_for_roi2':('official_coupon_amount','整体成交智能优惠券金额','元'),
    'total_ecom_platform_subsidy_amount_for_roi2':('official_subsidy_amount','平台补贴金额','元'),
    'stat_cost_for_overall_roi2':('comprehensive_cost','综合成本','元'),
    'total_prepay_and_pay_settle_overall_roi2_1h':('official_comprehensive_roi','官方综合ROI','倍'),
    'total_order_settle_amount_for_roi2_1h':('official_net_deal_amount','净成交金额','元'),
    'total_prepay_and_pay_settle_roi2_1h':('net_roi','净成交ROI','倍'),
    'total_order_settle_count_for_roi2_1h':('official_net_deal_orders','净成交订单数','单'),
    'total_order_real_settle_amount_for_roi2_1h':('official_actual_net_paid','用户实际支付净成交金额','元'),
    'total_refund_order_gmv_for_roi2_1h_rate':('official_refund_rate_1h_raw','1小时内退款率原值（单位待核）','来源原值'),
}
LIMITS=[
    '人工提供的查询上下文与响应，未认证平台来源或接口权限。',
    '计划类型、投放期间、营销目标和下单平台分别隔离，不能当作全部账户范围。',
    '保留官方返回ROI与金额原值，未把实际支付或整体成交金额推定为ROI分子。',
    '没有同口径累计金额和独立数据更新时间，不构造小时ROI；采集时间不替代数据时间。',
    '导入不声明操作历史、成本保障、计划状态或低延迟，不授予调控资格。',
]
