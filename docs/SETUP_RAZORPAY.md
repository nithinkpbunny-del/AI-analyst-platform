# Connecting Razorpay

The app reads your payments, orders, refunds and settlements. It only sends `GET` requests; it can never change
anything in Razorpay. Razorpay itself cannot limit an API key to reading, so **create a separate key for this app**.

1. In the Razorpay dashboard, switch to **Test mode** first if you want to try without real data
   (Account & Settings, **API keys**, Generate test key). For real data use **Live mode** and generate a live key.
2. Copy the **Key Id** (`rzp_test_…` or `rzp_live_…`) and the **Key Secret**. Razorpay shows the secret only once.
3. In the app: **Connections > Add data > Connect Razorpay**. Paste both and press **Check and connect**. The app
   asks Razorpay for one payment to prove the key works; nothing is saved if it does not.
4. The first read goes back 365 days (`RAZORPAY_HISTORY_DAYS`). After that only new and changed rows are fetched.
5. Rotating or revoking the key: open the connection and press **Enter a new key** (or **Replace the API key**).
6. Removing the connection deletes the stored secret and the cached data at once. Revoke the key in Razorpay too.

Amounts are kept in **paise**, exactly as Razorpay sends them, and the business dictionary converts to rupees.

**Trying it without a Razorpay account** (development only): run `make fixtures`, then use the key id
`rzp_test_demoAbCd1234` with any secret of 8 or more characters. The app then reads the demo payments from a local
imitation of Razorpay.
