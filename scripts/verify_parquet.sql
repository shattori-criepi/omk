-- DuckDB verification queries. {{...}} paths are substituted by verify_parquet.py.
CREATE OR REPLACE TEMP VIEW sen66 AS SELECT * FROM read_parquet('{{SEN66_PATH}}', hive_partitioning = true);
CREATE OR REPLACE TEMP VIEW power AS SELECT * FROM read_parquet('{{POWER_PATH}}', hive_partitioning = true);
CREATE OR REPLACE TEMP VIEW cumulative_energy AS SELECT * FROM read_parquet('{{CUMULATIVE_PATH}}', hive_partitioning = true);
CREATE OR REPLACE TEMP VIEW interval_energy AS SELECT * FROM read_parquet('{{INTERVAL_PATH}}', hive_partitioning = true);

-- SECTION: SEN66: basic information
SELECT 'sen66' AS dataset, COUNT(*) AS record_count, MIN(measured_at) AS first_measured_at, MAX(measured_at) AS last_measured_at, COUNT(DISTINCT device_id) AS device_count, string_agg(DISTINCT device_id, ', ' ORDER BY device_id) AS device_ids FROM sen66;
-- SECTION: B-route instantaneous power: basic information
SELECT 'broute_power' AS dataset, COUNT(*) AS record_count, MIN(measured_at) AS first_measured_at, MAX(measured_at) AS last_measured_at, COUNT(DISTINCT device_id) AS device_count, string_agg(DISTINCT device_id, ', ' ORDER BY device_id) AS device_ids FROM power;
-- SECTION: B-route cumulative energy: basic information
SELECT 'broute_cumulative_energy' AS dataset, COUNT(*) AS record_count, MIN(metered_at) AS first_metered_at, MAX(metered_at) AS last_metered_at, COUNT(DISTINCT device_id) AS device_count, string_agg(DISTINCT device_id, ', ' ORDER BY device_id) AS device_ids FROM cumulative_energy;
-- SECTION: B-route interval energy: basic information
SELECT 'broute_interval_energy' AS dataset, COUNT(*) AS record_count, MIN(start_at) AS first_start_at, MAX(end_at) AS last_end_at, COUNT(DISTINCT device_id) AS device_count, string_agg(DISTINCT device_id, ', ' ORDER BY device_id) AS device_ids FROM interval_energy;

-- SECTION: SEN66: records by date partition
SELECT date, COUNT(*) AS record_count FROM sen66 GROUP BY date ORDER BY date;
-- SECTION: B-route instantaneous power: records by date partition
SELECT date, COUNT(*) AS record_count FROM power GROUP BY date ORDER BY date;
-- SECTION: B-route cumulative energy: records by date partition
SELECT date, COUNT(*) AS record_count FROM cumulative_energy GROUP BY date ORDER BY date;
-- SECTION: B-route interval energy: records by date partition
SELECT date, COUNT(*) AS record_count FROM interval_energy GROUP BY date ORDER BY date;

-- SECTION: B-route instantaneous power: NULL counts
SELECT COUNT(*) FILTER (WHERE measured_at IS NULL) AS measured_at_nulls, COUNT(*) FILTER (WHERE net_power_w IS NULL) AS net_power_w_nulls FROM power;
-- SECTION: B-route instantaneous power: hourly statistics (JST)
SELECT date_trunc('hour', measured_at) AS hour, COUNT(*) AS record_count, ROUND(AVG(net_power_w), 1) AS avg_power_w, MIN(net_power_w) AS min_power_w, MAX(net_power_w) AS max_power_w FROM power GROUP BY hour ORDER BY hour;

-- SECTION: SEN66: NULL counts
SELECT COUNT(*) FILTER (WHERE measured_at IS NULL) AS measured_at_nulls, COUNT(*) FILTER (WHERE temperature_c IS NULL) AS temperature_c_nulls, COUNT(*) FILTER (WHERE relative_humidity_pct IS NULL) AS relative_humidity_pct_nulls, COUNT(*) FILTER (WHERE co2_ppm IS NULL) AS co2_ppm_nulls, COUNT(*) FILTER (WHERE pm2_5_ug_m3 IS NULL) AS pm2_5_ug_m3_nulls FROM sen66;
-- SECTION: SEN66: hourly statistics (JST)
SELECT date_trunc('hour', measured_at) AS hour, COUNT(*) AS record_count, ROUND(AVG(temperature_c), 2) AS avg_temperature_c, ROUND(AVG(relative_humidity_pct), 2) AS avg_relative_humidity_pct, ROUND(AVG(co2_ppm), 1) AS avg_co2_ppm, ROUND(AVG(pm2_5_ug_m3), 2) AS avg_pm2_5_ug_m3 FROM sen66 GROUP BY hour ORDER BY hour;

-- SECTION: SEN66: per-device receiving interval statistics in seconds
WITH intervals AS (SELECT device_id, epoch(measured_at - lag(measured_at) OVER (PARTITION BY device_id ORDER BY measured_at)) AS interval_seconds FROM sen66) SELECT device_id, COUNT(interval_seconds) AS interval_count, ROUND(MIN(interval_seconds), 3) AS min_seconds, ROUND(AVG(interval_seconds), 3) AS avg_seconds, ROUND(median(interval_seconds), 3) AS median_seconds, ROUND(MAX(interval_seconds), 3) AS max_seconds, COUNT(*) FILTER (WHERE interval_seconds > 15) AS over_15_seconds_count FROM intervals GROUP BY device_id ORDER BY device_id;
-- SECTION: SEN66: receiving interval distribution in seconds
WITH intervals AS (SELECT epoch(measured_at - lag(measured_at) OVER (PARTITION BY device_id ORDER BY measured_at)) AS interval_seconds FROM sen66) SELECT CASE WHEN interval_seconds < 1 THEN '< 1 sec' WHEN interval_seconds < 5 THEN '1-5 sec' WHEN interval_seconds < 9 THEN '5-9 sec' WHEN interval_seconds < 11 THEN '9-11 sec' WHEN interval_seconds <= 15 THEN '11-15 sec' ELSE '> 15 sec' END AS interval_band, COUNT(*) AS interval_count FROM intervals WHERE interval_seconds IS NOT NULL GROUP BY interval_band ORDER BY CASE interval_band WHEN '< 1 sec' THEN 1 WHEN '1-5 sec' THEN 2 WHEN '5-9 sec' THEN 3 WHEN '9-11 sec' THEN 4 WHEN '11-15 sec' THEN 5 ELSE 6 END;

-- SECTION: B-route interval energy: summary
SELECT COUNT(*) AS record_count, MIN(start_at) AS first_start_at, MAX(end_at) AS last_end_at, SUM(import_energy_kwh) AS total_import_kwh, SUM(export_energy_kwh) AS total_export_kwh FROM interval_energy;
-- SECTION: B-route interval energy: quality status counts
SELECT quality_status, COUNT(*) AS record_count FROM interval_energy GROUP BY quality_status ORDER BY quality_status;
-- SECTION: B-route interval energy: 30-minute interval details
SELECT device_id, start_at, end_at, import_energy_kwh, export_energy_kwh, quality_status FROM interval_energy ORDER BY device_id, end_at;

-- SECTION: B-route cumulative energy: values
SELECT device_id, metered_at, cumulative_energy_import_kwh, cumulative_energy_export_kwh FROM cumulative_energy ORDER BY device_id, metered_at;
-- SECTION: B-route cumulative energy: NULL counts
SELECT COUNT(*) FILTER (WHERE device_id IS NULL) AS device_id_nulls, COUNT(*) FILTER (WHERE metered_at IS NULL) AS metered_at_nulls, COUNT(*) FILTER (WHERE cumulative_energy_import_kwh IS NULL) AS cumulative_energy_import_kwh_nulls, COUNT(*) FILTER (WHERE cumulative_energy_export_kwh IS NULL) AS cumulative_energy_export_kwh_nulls FROM cumulative_energy;

-- SECTION: Cumulative-energy deltas compared with interval energy
WITH cumulative_deltas AS (SELECT device_id, metered_at AS end_at, cumulative_energy_import_kwh - lag(cumulative_energy_import_kwh) OVER (PARTITION BY device_id ORDER BY metered_at) AS calculated_import_kwh, cumulative_energy_export_kwh - lag(cumulative_energy_export_kwh) OVER (PARTITION BY device_id ORDER BY metered_at) AS calculated_export_kwh FROM cumulative_energy) SELECT interval_energy.end_at, interval_energy.import_energy_kwh AS interval_import_kwh, cumulative_deltas.calculated_import_kwh, interval_energy.import_energy_kwh - cumulative_deltas.calculated_import_kwh AS import_difference_kwh, interval_energy.export_energy_kwh AS interval_export_kwh, cumulative_deltas.calculated_export_kwh, interval_energy.export_energy_kwh - cumulative_deltas.calculated_export_kwh AS export_difference_kwh, CASE WHEN ABS(interval_energy.import_energy_kwh - cumulative_deltas.calculated_import_kwh) <= 0.000001 AND ABS(interval_energy.export_energy_kwh - cumulative_deltas.calculated_export_kwh) <= 0.000001 THEN 'matched' ELSE 'not_matched' END AS match_status FROM interval_energy LEFT JOIN cumulative_deltas ON interval_energy.device_id = cumulative_deltas.device_id AND interval_energy.end_at = cumulative_deltas.end_at ORDER BY interval_energy.device_id, interval_energy.end_at;

-- SECTION: SEN66 and B-route power ASOF join (15 sec or less: usual; 15-30 sec: update delay; over 30 sec: stale or missing candidate)
WITH joined AS (SELECT sen.measured_at AS sen_measured_at, power.measured_at AS power_measured_at FROM sen66 AS sen ASOF LEFT JOIN power AS power ON sen.measured_at >= power.measured_at), delays AS (SELECT date_diff('millisecond', power_measured_at, sen_measured_at) / 1000.0 AS delay_seconds FROM joined) SELECT COUNT(*) AS sen66_record_count, COUNT(delay_seconds) AS joined_count, COUNT(*) - COUNT(delay_seconds) AS unmatched_count, ROUND(100.0 * COUNT(delay_seconds) / NULLIF(COUNT(*), 0), 2) AS join_rate_pct, ROUND(MIN(delay_seconds), 3) AS min_delay_seconds, ROUND(AVG(delay_seconds), 3) AS avg_delay_seconds, ROUND(MAX(delay_seconds), 3) AS max_delay_seconds, COUNT(*) FILTER (WHERE delay_seconds > 15) AS over_15_seconds_count, COUNT(*) FILTER (WHERE delay_seconds > 30) AS over_30_seconds_count FROM delays;
