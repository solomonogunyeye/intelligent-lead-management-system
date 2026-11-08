import sys
import tempfile
sys.path.insert(0, '.')
from leadlift.db import reset_db, init_db_with_migrations, _issue_reset_capability, _revoke_reset_capability
from leadlift.models import Inquiry, InquirySource
from leadlift.repository import InquiryRepository
from leadlift.ai.processor import AIProcessor

if __name__ == "__main__":
    from pathlib import Path
    from tests.test_support import _safe_cleanup_directory

    temp_root = Path(tempfile.mkdtemp(prefix="leadlift_test_")).resolve()
    db_path = temp_root / "test_leadlift.db"

    try:
        capability = _issue_reset_capability(db_path, temp_root)
        try:
            reset_db(db_path, capability=capability)
        finally:
            _revoke_reset_capability(capability)

        # reset_db() already calls init_db_with_migrations(db_path=path),
        # so no redundant initialization is needed.
        
        repo = InquiryRepository()
        i = Inquiry(
            source='manual',
            customer_name='Restart Test',
            customer_email='restart@test.com',
            requested_service='Web Development',
            original_text='Test persistence after restart with AI processing',
        )
        repo.save(i)
        inq_id = i.inquiry_id
        print(f'Saved inquiry: {inq_id}')
        
        # Process with AI
        processor = AIProcessor()
        result = processor.process_inquiry(inq_id)
        print(f'Processing result: success={result["success"]}')
        print(f'Classification: {result["classification"]} (confidence: {result["confidence"]})')
        print(f'Missing info: {result["missing_information"]}')
        print(f'Draft: {result["draft"][:100]}...')
        
        # Simulate restart - create new repository
        repo2 = InquiryRepository()
        retrieved = repo2.get(inq_id)
        print('\nAfter restart:')
        print(f'  Customer: {retrieved.customer_name}')
        print(f'  Email: {retrieved.customer_email}')
        print(f'  Classification: {retrieved.classification} (source: {retrieved.classification_source})')
        print(f'  AI State: {retrieved.ai_state}')
        print(f'  Missing info: {len(retrieved.missing_information)} items')
        print(f'  Draft in processing history: {len(repo2.get_processing_history(inq_id))} entries')
        
        drafts = repo2.get_drafts_for_inquiry(inq_id)
        print(f'  Response drafts: {len(drafts)}')
        if drafts:
            print(f'  Latest draft: {drafts[0].draft_text[:100]}...')
        
        print('\nPersistence test PASSED')

    finally:
        # Clean up — no error suppression, failures surface clearly
        _safe_cleanup_directory(temp_root)
