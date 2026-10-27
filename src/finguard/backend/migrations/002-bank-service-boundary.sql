-- 保留 001 的历史。空的原型表退出新库；已有数据移入只作迁移来源的归档。
DO $$
DECLARE
    names text[] := ARRAY['bank_accounts','bank_recipients','bank_products','bank_positions',
                          'bank_services','bank_commands','bank_ledger','bank_faults'];
    item text;
    has_rows boolean := false;
    populated boolean;
    archive_name text;
BEGIN
    IF to_regclass(format('%I.bank_accounts', current_schema())) IS NULL THEN
        RETURN;
    END IF;
    FOREACH item IN ARRAY names LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I LIMIT 1)', current_schema(), item)
        INTO populated;
        has_rows := has_rows OR populated;
    END LOOP;
    IF has_rows THEN
        archive_name := 'legacy_bank_' || substr(md5(current_schema()), 1, 20);
        EXECUTE format('CREATE SCHEMA %I', archive_name);
        FOREACH item IN ARRAY names LOOP
            EXECUTE format('ALTER TABLE %I.%I SET SCHEMA %I', current_schema(), item, archive_name);
        END LOOP;
    ELSE
        DROP TABLE bank_faults, bank_ledger, bank_commands, bank_services,
                   bank_positions, bank_products, bank_recipients, bank_accounts;
    END IF;
END $$;
