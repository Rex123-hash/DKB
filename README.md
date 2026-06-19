# DukanBook AI VoiceBot

DukanBook helps a local shopkeeper keep accounts without a paper register. This project
adds a voice and text assistant on top of that idea. The shopkeeper can simply talk to it
in Hindi, English, or a mix of the two, and it writes the khata, answers everyday business
questions, and sets payment reminders.

## Why we are building this

Most small shops in India still keep their hisaab in a paper book. The accounting apps that
already exist are costly and hard to use, and many shopkeepers are far more comfortable
speaking than typing. We wanted something that feels as easy as talking to a munshi, so a
shopkeeper does not have to learn complicated software to run their own accounts.

## What it can do

The assistant takes care of the daily money work of a shop.

- Records udhaar and payments for every customer and supplier, with a running balance.
- Answers questions about GST, income tax, loans, licences, stock and general business.
- Sets payment and call reminders by date and time.
- Handles small things too, like the current weather of a city and basic maths.
- Works by voice and by text, in Hindi, English and Hinglish.

When it is not sure about something, it says so instead of giving a wrong answer.

## How it is built, in short

The ledger, the assistant and the reminders are kept as separate parts, so each one stays
simple to use and to test. The accounts are stored in a small database. The assistant reads
a request, does the right job, and replies in the same language the shopkeeper used. The
business answers come from a set of reference notes we prepared, which keeps the information
reliable.

## Running it

```
pip install -r requirements.txt
```

Configuration goes in a local `.env` file. A sample is provided in `.env.example`.


