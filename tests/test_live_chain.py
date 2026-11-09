from datetime import datetime,timezone,timedelta
from dataclasses import replace
from unittest.mock import Mock
import pytest
from live_chain import unwrap,occ_info,ChainCollector
from quant_agent import DataError
from settings import Settings
from market_session import calendar,ET


def test_occ():
    assert occ_info('SPY261106C00600000')[2:]==('C',600.0)


def test_pagination_shape():
    assert unwrap({'pagination_key':'outer','data':{'list':[{'symbol':'X'}]}})[1]=='outer'


def test_unknown_shape_fails():
    with pytest.raises(DataError): unwrap({'unexpected':[]})


def test_missing_cursor_fails():
    with pytest.raises(DataError,match='pagination'): unwrap({'list':[],'has_more':True})


def make_collector():
    adapter=Mock();adapter.settings=Settings(dte_days=1)
    today=datetime.now(timezone.utc).astimezone(ET).date()
    day=calendar().date_to_session(str(today),direction='next').date()
    now=calendar().session_open(str(day)).to_pydatetime()
    return ChainCollector(adapter,'SPY',600,now=now),adapter,day


def test_exact_expiry_filter_no_end_date():
    collector,adapter,day=make_collector()
    adapter.request.return_value={'list':[]}
    collector.step(Mock())
    args=adapter.request.call_args.kwargs
    assert args['start_date']==str(day)
    assert 'end_date' not in args  # end_date is a lower bound, not an upper bound.


def test_chain_does_not_publish_partial_pages():
    collector,adapter,day=make_collector()
    sym='SPY'+day.strftime('%y%m%d')+'C00600000'
    adapter.request.return_value={'list':[{'symbol':sym,'multiplier':100}],'pagination_key':'next'}
    collector.step(Mock())
    assert not collector.done
    with pytest.raises(DataError,match='Repeated'): collector.step(Mock())


def test_scope_missing_data_fails():
    collector,adapter,day=make_collector()
    collector.contracts={'SPY'+day.strftime('%y%m%d')+'C00600000':{'multiplier':100}}
    collector.snapshots={k:{'iv':.2} for k in collector.contracts}
    with pytest.raises(DataError,match='missing IV/OI/multiplier'): collector._finish()


def test_repeated_cursor_without_duplicate_contract_fails():
    collector,adapter,day=make_collector()
    adapter.request.side_effect=[{'list':[{'symbol':'SPY'+day.strftime('%y%m%d')+'C00600000'}],'pagination_key':'x'},
                                {'list':[],'pagination_key':'x'}]
    collector.step(Mock())
    with pytest.raises(DataError,match='pagination'): collector.step(Mock())
