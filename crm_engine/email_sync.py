"""python -m crm_engine.email_sync [--migrate | --connect]. Never sends email."""
import argparse

from crm_engine import db
from crm_engine.email_review import email_lock, sync
from crm_engine.gmail_client import GmailClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--migrate', action='store_true')
    modes.add_argument('--connect', action='store_true')
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
            count = sync(client)
            print(f'Email sync complete: {count} conversations refreshed. No email sent.')
    except Exception as exc:
        print(f'Email setup/sync failed ({type(exc).__name__}). See Email Review in the CRM for setup and sync status.')
        if db.email_ready():
            from crm_engine.gmail_client import MAILBOX
            db.save_email_sync(MAILBOX, None,
                'Email connection or sync failed. Reconnect Gmail or use Sync now to retry.')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
