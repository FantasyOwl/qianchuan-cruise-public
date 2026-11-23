"""Validated native write contracts, distinct from legacy UI wording."""
from decimal import Decimal
from .actuation import amount


def chase_contract(rule,roi_basis=None,*,delivery_entry=None,marketing_services_mode=None,for_browser_readback=False):
    from .chase_audience import configured
    if configured(rule) and not for_browser_readback:raise ValueError('本条有定向规则，请使用API取数、网页追投；API创建不能忽略定向')
    kind=rule.get('chase_type')
    if kind=='控成本追投':
        budget=amount(rule.get('daily_budget'));value=amount(rule.get('bid_value'))
        goal=rule.get('optimization_target')
        body={'budget':float(budget),'smart_bid_type':'SMART_BID_CUSTOM',
            'external_action':'AD_CONVERT_TYPE_LIVE_SUCCESSORDER_PAY'}
        if goal=='直播间成交':
            if value>budget:raise ValueError('成交出价不能超过整批日预算')
            body['bid']=float(value);label='直播间成交';value_label='成交出价（元）'
        elif goal in {'综合ROI','综合营销ROI','支付ROI','净成交ROI'}:
            resolved=goal if goal not in {'综合ROI','综合营销ROI'} else roi_basis
            if resolved not in {'支付ROI','净成交ROI'}:raise ValueError('旧“综合ROI”未能唯一识别官方口径，请在规则中明确选择支付ROI或净成交ROI')
            body.update(deep_external_action='AD_CONVERT_TYPE_LIVE_PAY_ROI' if resolved=='支付ROI' else 'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI',roi2_goal=float(value))
            if goal in {'综合ROI','综合营销ROI'} and delivery_entry=='chengfang' and resolved!='净成交ROI':raise ValueError('乘方综合营销ROI与当前返回的深度目标存在冲突，未换成支付ROI追投')
            label='综合营销ROI' if goal in {'综合ROI','综合营销ROI'} and delivery_entry=='chengfang' else resolved
            value_label=label+'目标（倍）'
        else:raise ValueError('未知的追投优化目标')
        return {'body':body,'budget':str(budget),'budget_kind':'daily','goal_label':label,
            'native_deep_external_action':body.get('deep_external_action'),'delivery_entry':delivery_entry,
            'marketing_services_mode':marketing_services_mode,
            'value_label':value_label,'value':str(value),'auto_stop_hours':None,
            'notes':(['乘方界面按综合营销ROI确认；API原生深度目标沿用本母计划现有ROI追投返回的类型，不改成支付ROI。'] if label=='综合营销ROI' else [])}
    if kind=='放量追投':
        budget=amount(rule.get('total_budget'));hours=amount(rule.get('control_duration'))
        if not Decimal('.5')<=hours<=24 or hours%Decimal('.5'):raise ValueError('放量追投时长须为0.5至24小时，步进0.5小时')
        return {'body':{'budget':float(budget),'smart_bid_type':'SMART_BID_CONSERVATIVE'},
            'budget':str(budget),'budget_kind':'total','goal_label':'放量追投','value_label':None,'value':None,
            'auto_stop_hours':str(hours),'notes':['直播母计划追投API没有原生到期字段；本单将在创建后按实际任务ID安排本地定时暂停。须保持本机服务运行；离线期间不能保证准时暂停。']}
    raise ValueError('未知的追投方式')
