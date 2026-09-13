-- 002_repair_package_no.sql
--
-- Repairs data written by the first version of the e-GP list parser, which
-- assumed every brief cell was [nature, package_no, description]. Most live
-- rows carry no package number at all, so for roughly 79% of tenders the
-- description was stored as the package number and the description left NULL.
--
-- The parser now detects a package number by shape. It cannot repair existing
-- rows on its own: it correctly yields NULL for these tenders, and the upsert
-- deliberately never lets a NULL erase a stored value.
--
-- Package numbers on this source are always a single whitespace-free token
-- ("PSWSC-6145", "LGED/GOBM/SRJ/26-27/RW-49"). Any value containing a space is
-- prose that belongs in description.

-- Keep the prose where it belongs when the description was left empty.
UPDATE tenders
   SET description = package_no
 WHERE package_no LIKE '% %'
   AND (description IS NULL OR description = '');

UPDATE tenders
   SET package_no = NULL
 WHERE package_no LIKE '% %';

-- Phantom versions: rows recorded when the canonical hash moved but no field
-- did, caused by NUMERIC(18,2) returning a different Decimal scale than the
-- parser produced. They represent no real amendment and would surface to users
-- as corrigenda. The hash now quantizes money, and the upsert refuses to write
-- a version with an empty diff, so no new ones can appear.
--
-- Repoint any tender whose current version is a phantom, before deleting.
UPDATE tenders t
   SET current_version_id = (
           SELECT tv.id
             FROM tender_versions tv
            WHERE tv.tender_id = t.id
              AND NOT (tv.version_no > 1 AND tv.changed_fields = '{}'::jsonb)
            ORDER BY tv.version_no DESC
            LIMIT 1
       )
 WHERE t.current_version_id IN (
           SELECT id FROM tender_versions
            WHERE version_no > 1 AND changed_fields = '{}'::jsonb
       );

DELETE FROM tender_versions
 WHERE version_no > 1
   AND changed_fields = '{}'::jsonb;

-- Rows whose package_no changed now disagree with their stored canonical_hash.
-- The upsert's self-healing path corrects that on the next sweep: it sees a
-- hash change with an empty field diff, stores the corrected hash and logs,
-- rather than inventing a corrigendum.
