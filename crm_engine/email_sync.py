"""python -m crm_engine.email_sync [--migrate | --connect]. Never sends email."""
import argparse

from crm_engine import db
from crm_engine.email_review import email_lock, sync, EmailBusy
from crm_engine.gmail_client import GmailClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--migrate', action='store_true')
    modes.add_argument('--connect', action='store_true')
    modes.add_argument('--review-year', action='store_true', help='Read the past year and prepare deal/relationship opportunities.')
    args = parser.parse_args()
    try:
        with email_lock():
            if args.migrate:
                backup = db.migrate_email()
                print(f'Email migration complete. Backup: {backup or "new database"}')
                return
            client = GmailClient.connect(interactive=args.connect)
            if args.connect:
                print('Gmail connected and identity verified.')
            if args.review_year:
                from crm_engine.opportunity_scan import review_year
                result = review_year(client)
                print('Opportunity review:', result['state'], '. No messages sent.')
                return
            count = sync(client)
            print(f'Email sync complete: {count} conversations refreshed. No email sent.')
            try:
                from crm_engine.gv_gmail_import import sync_google_voice
                gv = sync_google_voice(client, since_days=7)
                print(f'Google Voice: {gv["matched"]} matched, {gv["unmatched"]} unmatched new texts.')
            except Exception as gv_exc:
                print(f'Google Voice sync skipped ({type(gv_exc).__name__}).')
    except Exception as exc:
        if isinstance(exc, EmailBusy):
            print('An email review or synchronization is already running. This scheduled run was skipped.')
            return
        print(f'Email setup/sync failed ({type(exc).__name__}). See Email Review in the CRM for setup and sync status.')
        if db.email_ready():
            from crm_engine.gmail_client import MAILBOX
            db.save_email_sync(MAILBOX, None,
                'Email connection or sync failed. Reconnect Gmail or use Sync now to retry.')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
