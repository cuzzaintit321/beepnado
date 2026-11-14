import unittest
from unittest.mock import patch, Mock
from fastapi import HTTPException
from app import main

class PricingCatalogTests(unittest.TestCase):
    def test_catalog(self):
        self.assertEqual([(p['id'],p['monthly'],p['annual'],p['monitors'],p['interval_seconds'])
            for p in main.pricing_catalog()],[
            ('free',0,0,50,300),('pro',9,90,50,60),('team',29,290,150,30),('scale',59,590,300,15)])

    def test_legacy_mapping(self):
        with patch.object(main,'PRICE_IDS',{'pro':'legacy_pro','team':'legacy_team'}), patch.object(main,'NEW_PRICE_IDS',{('scale','year'):'scale_year'}):
            self.assertEqual(main.price_mapping(),{'legacy_pro':'pro','legacy_team':'team','scale_year':'scale'})

    def test_wrong_price_rejected_before_checkout(self):
        stripe=Mock()
        stripe.Price.retrieve.return_value={'active':True,'currency':'usd','unit_amount':1200,
            'recurring':{'interval':'month','interval_count':1},'livemode':False}
        with patch.object(main,'NEW_PRICE_IDS',{('pro','month'):'new_pro'}),patch.object(main,'STRIPE_LIVE_MODE',False):
            with self.assertRaises(HTTPException) as err:
                main.checkout_session(stripe,1,'pro','month')
            self.assertEqual(err.exception.status_code,503)
            stripe.checkout.Session.create.assert_not_called()
