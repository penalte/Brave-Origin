"""Validate signed OIDC tokens through the real verifier, without a live IdP."""
import asyncio
import importlib.util
import json
import time
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

spec = importlib.util.spec_from_file_location('manager', '/usr/local/bin/session-manager.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
config = m.Config({'APP_URL':'https://web.example.test', 'OIDC_ISSUER_URL':'https://id.example.test',
                   'OIDC_CLIENT_ID':'client', 'OIDC_CLIENT_SECRET':'secret', 'OIDC_ALLOWED_GROUPS':'browser'})
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
jwk.update(kid='test', alg='RS256', use='sig')

class Response:
    def __init__(self, data): self.data = data
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def raise_for_status(self): pass
    async def json(self): return self.data

class HTTP:
    def __init__(self, token): self.token = token
    def get(self, url, **kwargs):
        if url.endswith('openid-configuration'):
            return Response({'issuer':config.issuer, 'authorization_endpoint':config.issuer+'/authorize',
                             'token_endpoint':config.issuer+'/token', 'jwks_uri':config.issuer+'/keys'})
        return Response({'keys':[jwk]})
    def post(self, url, data, **kwargs):
        assert data['code_verifier'] == 'verifier' and data['redirect_uri'] == 'https://web.example.test:9443/auth/callback'
        return Response({'id_token':self.token})

async def main():
    claims = {'iss':config.issuer,'sub':'alice','aud':'client','iat':int(time.time()),
              'exp':int(time.time())+300,'nonce':'nonce','groups':['browser']}
    async def check(overrides, accepted=False, signing_key=key):
        token = jwt.encode({**claims, **overrides}, signing_key, algorithm='RS256', headers={'kid':'test'})
        try:
            result = await m.OIDC(config).authenticate(HTTP(token),'code',
                    {'verifier':'verifier','nonce':'nonce','origin':'https://web.example.test:9443'})
        except (ValueError, jwt.PyJWTError):
            assert not accepted, overrides
        else:
            assert accepted and result['sub'] == 'alice', overrides
    await check({}, True)
    for changed in ({'iss':'https://attacker.test'},{'aud':'other'},{'nonce':'wrong'},
                    {'exp':1},{'iat':int(time.time())+3600},{'groups':[]}, {'sub':''},
                    {'aud':['client','other']},{'azp':'other'}):
        await check(changed)
    await check({}, signing_key=rsa.generate_private_key(public_exponent=65537,key_size=2048))
    print('PASS: signed OIDC token, issuer, audience, nonce, expiry, issued-at, groups, subject, azp and wrong-key rejection')

asyncio.run(main())
