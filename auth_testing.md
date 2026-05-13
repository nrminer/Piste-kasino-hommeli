# Auth Testing Notes

## Customer
- URL: `/asiakas`
- Demo login: `Test Player` / `test123`
- Login API: `POST /api/customer/login`
- Browser storage key: `cust_session`

## Operator
- URL: `/operator`
- Demo password: `operator123`
- Login API: `POST /api/operator/login`
- Browser storage key: `operator_token`
- Protected operator APIs require `Authorization: Bearer <token>`.

## Current validation
- Iteration 15 backend auth regressions passed for both customer and operator logins.
- Frontend browser checks confirmed operator login opens the unified Texas Hold'em tab.