


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


CREATE SCHEMA IF NOT EXISTS "public";


ALTER SCHEMA "public" OWNER TO "pg_database_owner";


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE OR REPLACE FUNCTION "public"."update_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."update_updated_at"() OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."ai_cache" (
    "cache_key" "text" NOT NULL,
    "response" "text" NOT NULL,
    "provider" "text",
    "model" "text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "expires_at" timestamp with time zone NOT NULL
);


ALTER TABLE "public"."ai_cache" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."audit_log" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "action" "text" NOT NULL,
    "resource_type" "text",
    "resource_id" "text",
    "details" "jsonb",
    "ip_address" "text",
    "user_agent" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."audit_log" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."jobs" (
    "job_id" "text" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "title" "text" NOT NULL,
    "company" "text" NOT NULL,
    "location" "text",
    "description" "text",
    "apply_url" "text",
    "source" "text",
    "match_score" real DEFAULT 0,
    "ats_score" real DEFAULT 0,
    "hiring_manager_score" real DEFAULT 0,
    "tech_recruiter_score" real DEFAULT 0,
    "matched_resume" "text",
    "tailored_pdf_path" "text",
    "cover_letter_pdf_path" "text",
    "resume_doc_url" "text",
    "resume_s3_url" "text",
    "cover_letter_s3_url" "text",
    "linkedin_contacts" "text",
    "application_status" "text" DEFAULT 'New'::"text",
    "first_seen" timestamp with time zone DEFAULT "now"() NOT NULL,
    "last_seen" timestamp with time zone DEFAULT "now"() NOT NULL,
    "tailoring_model" "text",
    "cover_letter_model" "text",
    "job_hash" "text",
    "resume_version" integer DEFAULT 1,
    "is_expired" boolean DEFAULT false,
    "key_matches" "jsonb" DEFAULT '[]'::"jsonb",
    "gaps" "jsonb" DEFAULT '[]'::"jsonb",
    "match_reasoning" "text",
    "canonical_hash" "text",
    "base_ats_score" integer,
    "base_hm_score" integer,
    "base_tr_score" integer,
    "tailored_ats_score" integer,
    "tailored_hm_score" integer,
    "tailored_tr_score" integer,
    "final_score" double precision,
    "score_version" integer DEFAULT 1,
    "scored_at" timestamp with time zone,
    "score_status" "text" DEFAULT 'pending'::"text",
    "writing_quality_score" double precision
);


ALTER TABLE "public"."jobs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."jobs_raw" (
    "job_hash" "text" NOT NULL,
    "title" "text" NOT NULL,
    "company" "text" NOT NULL,
    "description" "text",
    "location" "text",
    "apply_url" "text",
    "source" "text" NOT NULL,
    "experience_level" "text",
    "job_type" "text",
    "query_hash" "text",
    "scraped_at" timestamp with time zone DEFAULT "now"(),
    "canonical_hash" "text"
);


ALTER TABLE "public"."jobs_raw" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."pipeline_adjustments" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "adjustment_type" "text" NOT NULL,
    "risk_level" "text" NOT NULL,
    "status" "text" DEFAULT 'pending'::"text" NOT NULL,
    "payload" "jsonb" NOT NULL,
    "previous_state" "jsonb",
    "reason" "text" NOT NULL,
    "evidence" "jsonb",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "applied_at" timestamp with time zone,
    "reverted_at" timestamp with time zone,
    "reviewed_by" "uuid",
    "run_id" "uuid",
    "cooldown_until" timestamp with time zone
);


ALTER TABLE "public"."pipeline_adjustments" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."pipeline_metrics" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "run_date" "date" NOT NULL,
    "execution_id" "text",
    "scraper_name" "text" NOT NULL,
    "jobs_found" integer DEFAULT 0,
    "jobs_matched" integer DEFAULT 0,
    "jobs_tailored" integer DEFAULT 0,
    "duration_seconds" integer,
    "apify_cost_cents" integer DEFAULT 0,
    "error_message" "text",
    "created_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."pipeline_metrics" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."pipeline_runs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "started_at" timestamp with time zone,
    "completed_at" timestamp with time zone,
    "jobs_scraped" integer DEFAULT 0,
    "jobs_new" integer DEFAULT 0,
    "jobs_scored" integer DEFAULT 0,
    "jobs_matched" integer DEFAULT 0,
    "jobs_tailored" integer DEFAULT 0,
    "avg_base_score" double precision,
    "avg_final_score" double precision,
    "avg_writing_quality" double precision,
    "active_adjustments" "jsonb",
    "scraper_stats" "jsonb",
    "model_stats" "jsonb",
    "status" "text" DEFAULT 'running'::"text"
);


ALTER TABLE "public"."pipeline_runs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."pipeline_tasks" (
    "task_id" "text" NOT NULL,
    "user_id" "uuid",
    "status" "text" DEFAULT 'pending'::"text" NOT NULL,
    "result" "jsonb",
    "error" "text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "payload" "jsonb"
);


ALTER TABLE "public"."pipeline_tasks" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."prompt_versions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "prompt_name" "text" NOT NULL,
    "version" integer NOT NULL,
    "content" "text" NOT NULL,
    "active_from" timestamp with time zone DEFAULT "now"(),
    "active_to" timestamp with time zone,
    "metrics" "jsonb",
    "created_by" "text" DEFAULT 'manual'::"text"
);


ALTER TABLE "public"."prompt_versions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."runs" (
    "run_id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "run_date" "date" NOT NULL,
    "run_time" time without time zone NOT NULL,
    "raw_jobs" integer DEFAULT 0,
    "unique_jobs" integer DEFAULT 0,
    "matched_jobs" integer DEFAULT 0,
    "resumes_generated" integer DEFAULT 0,
    "status" "text" DEFAULT 'running'::"text",
    "completed_at" timestamp with time zone,
    CONSTRAINT "valid_status" CHECK (("status" = ANY (ARRAY['running'::"text", 'completed'::"text", 'failed'::"text"])))
);


ALTER TABLE "public"."runs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."scrape_runs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "pipeline_run_id" "text" NOT NULL,
    "source" "text" NOT NULL,
    "status" "text" DEFAULT 'running'::"text",
    "jobs_found" integer DEFAULT 0,
    "jobs_new" integer DEFAULT 0,
    "new_job_hashes" "jsonb" DEFAULT '[]'::"jsonb",
    "error_message" "text",
    "blocked_reason" "text",
    "started_at" timestamp with time zone DEFAULT "now"(),
    "completed_at" timestamp with time zone
);


ALTER TABLE "public"."scrape_runs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."seen_jobs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "job_id" "text",
    "user_id" "uuid" NOT NULL,
    "canonical_hash" "text" NOT NULL,
    "first_seen" "date" NOT NULL,
    "last_seen" "date" NOT NULL,
    "title" "text",
    "company" "text",
    "score" double precision DEFAULT 0,
    "matched" boolean DEFAULT false
);


ALTER TABLE "public"."seen_jobs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."self_improvement_config" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "config_type" "text" NOT NULL,
    "config_data" "jsonb" NOT NULL,
    "applied_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."self_improvement_config" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."user_resumes" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "resume_key" "text" NOT NULL,
    "label" "text",
    "tex_content" "text",
    "google_doc_template_id" "text",
    "target_roles" "text"[],
    "template_style" "text" DEFAULT 'professional'::"text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."user_resumes" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."user_search_configs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "queries" "text"[],
    "locations" "jsonb",
    "geo_regions" "jsonb",
    "experience_levels" "text"[],
    "days_back" integer DEFAULT 7,
    "max_jobs_per_run" integer DEFAULT 15,
    "min_match_score" integer DEFAULT 60,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."user_search_configs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."users" (
    "id" "uuid" DEFAULT "auth"."uid"() NOT NULL,
    "email" "text" NOT NULL,
    "name" "text",
    "phone" "text",
    "location" "text",
    "visa_status" "text",
    "github" "text",
    "linkedin" "text",
    "website" "text",
    "work_authorizations" "jsonb",
    "candidate_context" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "gdpr_consent_at" timestamp with time zone,
    "gdpr_deletion_requested_at" timestamp with time zone,
    "last_seen_at" timestamp with time zone,
    "last_pipeline_run" timestamp with time zone,
    "notification_prefs" "jsonb" DEFAULT '{"sms": false, "email": true, "whatsapp": false}'::"jsonb"
);


ALTER TABLE "public"."users" OWNER TO "postgres";


ALTER TABLE ONLY "public"."ai_cache"
    ADD CONSTRAINT "ai_cache_pkey" PRIMARY KEY ("cache_key");



ALTER TABLE ONLY "public"."audit_log"
    ADD CONSTRAINT "audit_log_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."jobs"
    ADD CONSTRAINT "jobs_pkey" PRIMARY KEY ("job_id", "user_id");



ALTER TABLE ONLY "public"."jobs_raw"
    ADD CONSTRAINT "jobs_raw_pkey" PRIMARY KEY ("job_hash");



ALTER TABLE ONLY "public"."pipeline_adjustments"
    ADD CONSTRAINT "pipeline_adjustments_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pipeline_metrics"
    ADD CONSTRAINT "pipeline_metrics_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pipeline_runs"
    ADD CONSTRAINT "pipeline_runs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pipeline_tasks"
    ADD CONSTRAINT "pipeline_tasks_pkey" PRIMARY KEY ("task_id");



ALTER TABLE ONLY "public"."prompt_versions"
    ADD CONSTRAINT "prompt_versions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."runs"
    ADD CONSTRAINT "runs_pkey" PRIMARY KEY ("run_id");



ALTER TABLE ONLY "public"."scrape_runs"
    ADD CONSTRAINT "scrape_runs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."seen_jobs"
    ADD CONSTRAINT "seen_jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."seen_jobs"
    ADD CONSTRAINT "seen_jobs_user_id_canonical_hash_key" UNIQUE ("user_id", "canonical_hash");



ALTER TABLE ONLY "public"."self_improvement_config"
    ADD CONSTRAINT "self_improvement_config_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."self_improvement_config"
    ADD CONSTRAINT "self_improvement_config_user_id_config_type_key" UNIQUE ("user_id", "config_type");



ALTER TABLE ONLY "public"."user_resumes"
    ADD CONSTRAINT "user_resumes_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_resumes"
    ADD CONSTRAINT "user_resumes_user_id_resume_key_key" UNIQUE ("user_id", "resume_key");



ALTER TABLE ONLY "public"."user_search_configs"
    ADD CONSTRAINT "user_search_configs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_search_configs"
    ADD CONSTRAINT "user_search_configs_user_id_key" UNIQUE ("user_id");



ALTER TABLE ONLY "public"."users"
    ADD CONSTRAINT "users_email_key" UNIQUE ("email");



ALTER TABLE ONLY "public"."users"
    ADD CONSTRAINT "users_pkey" PRIMARY KEY ("id");



CREATE INDEX "idx_ai_cache_expires" ON "public"."ai_cache" USING "btree" ("expires_at");



CREATE INDEX "idx_audit_log_action" ON "public"."audit_log" USING "btree" ("action");



CREATE INDEX "idx_audit_log_created_at" ON "public"."audit_log" USING "btree" ("created_at");



CREATE INDEX "idx_audit_log_user_id" ON "public"."audit_log" USING "btree" ("user_id");



CREATE INDEX "idx_jobs_canonical_hash" ON "public"."jobs" USING "btree" ("canonical_hash");



CREATE INDEX "idx_jobs_company" ON "public"."jobs" USING "btree" ("user_id", "company");



CREATE INDEX "idx_jobs_first_seen" ON "public"."jobs" USING "btree" ("user_id", "first_seen");



CREATE INDEX "idx_jobs_match_score" ON "public"."jobs" USING "btree" ("user_id", "match_score");



CREATE INDEX "idx_jobs_raw_canonical_hash" ON "public"."jobs_raw" USING "btree" ("canonical_hash");



CREATE INDEX "idx_jobs_raw_scraped" ON "public"."jobs_raw" USING "btree" ("scraped_at");



CREATE INDEX "idx_jobs_raw_source_query" ON "public"."jobs_raw" USING "btree" ("source", "query_hash", "scraped_at");



CREATE INDEX "idx_jobs_user_id" ON "public"."jobs" USING "btree" ("user_id");



CREATE INDEX "idx_metrics_user_date" ON "public"."pipeline_metrics" USING "btree" ("user_id", "run_date");



CREATE INDEX "idx_runs_date" ON "public"."runs" USING "btree" ("user_id", "run_date");



CREATE INDEX "idx_runs_user_id" ON "public"."runs" USING "btree" ("user_id");



CREATE INDEX "idx_scrape_runs_pipeline" ON "public"."scrape_runs" USING "btree" ("pipeline_run_id");



CREATE INDEX "idx_scrape_runs_source" ON "public"."scrape_runs" USING "btree" ("source", "started_at" DESC);



CREATE INDEX "idx_user_resumes_user_id" ON "public"."user_resumes" USING "btree" ("user_id");



CREATE INDEX "idx_user_search_configs_user_id" ON "public"."user_search_configs" USING "btree" ("user_id");



CREATE OR REPLACE TRIGGER "user_resumes_updated_at" BEFORE UPDATE ON "public"."user_resumes" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at"();



CREATE OR REPLACE TRIGGER "user_search_configs_updated_at" BEFORE UPDATE ON "public"."user_search_configs" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at"();



CREATE OR REPLACE TRIGGER "users_updated_at" BEFORE UPDATE ON "public"."users" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at"();



ALTER TABLE ONLY "public"."audit_log"
    ADD CONSTRAINT "audit_log_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."jobs"
    ADD CONSTRAINT "jobs_job_hash_fkey" FOREIGN KEY ("job_hash") REFERENCES "public"."jobs_raw"("job_hash");



ALTER TABLE ONLY "public"."jobs"
    ADD CONSTRAINT "jobs_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."pipeline_adjustments"
    ADD CONSTRAINT "pipeline_adjustments_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id");



ALTER TABLE ONLY "public"."pipeline_metrics"
    ADD CONSTRAINT "pipeline_metrics_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id");



ALTER TABLE ONLY "public"."pipeline_runs"
    ADD CONSTRAINT "pipeline_runs_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id");



ALTER TABLE ONLY "public"."pipeline_tasks"
    ADD CONSTRAINT "pipeline_tasks_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id");



ALTER TABLE ONLY "public"."prompt_versions"
    ADD CONSTRAINT "prompt_versions_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id");



ALTER TABLE ONLY "public"."runs"
    ADD CONSTRAINT "runs_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."seen_jobs"
    ADD CONSTRAINT "seen_jobs_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id");



ALTER TABLE ONLY "public"."self_improvement_config"
    ADD CONSTRAINT "self_improvement_config_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id");



ALTER TABLE ONLY "public"."user_resumes"
    ADD CONSTRAINT "user_resumes_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."user_search_configs"
    ADD CONSTRAINT "user_search_configs_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE CASCADE;



CREATE POLICY "Anyone can read jobs_raw" ON "public"."jobs_raw" FOR SELECT USING (true);



CREATE POLICY "Service role full access" ON "public"."pipeline_metrics" USING (("auth"."role"() = 'service_role'::"text"));



CREATE POLICY "Service role full access" ON "public"."pipeline_tasks" USING (true);



CREATE POLICY "Service role full access" ON "public"."self_improvement_config" USING (("auth"."role"() = 'service_role'::"text"));



CREATE POLICY "Service role only" ON "public"."ai_cache" USING (("auth"."role"() = 'service_role'::"text"));



CREATE POLICY "Service role writes jobs_raw" ON "public"."jobs_raw" USING (("auth"."role"() = 'service_role'::"text"));



CREATE POLICY "Users read own config" ON "public"."self_improvement_config" FOR SELECT USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users read own metrics" ON "public"."pipeline_metrics" FOR SELECT USING (("auth"."uid"() = "user_id"));



ALTER TABLE "public"."ai_cache" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."audit_log" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "audit_log_insert" ON "public"."audit_log" FOR INSERT WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "audit_log_select" ON "public"."audit_log" FOR SELECT USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."jobs" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "jobs_delete" ON "public"."jobs" FOR DELETE USING (("user_id" = "auth"."uid"()));



CREATE POLICY "jobs_insert" ON "public"."jobs" FOR INSERT WITH CHECK (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."jobs_raw" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "jobs_select" ON "public"."jobs" FOR SELECT USING (("user_id" = "auth"."uid"()));



CREATE POLICY "jobs_update" ON "public"."jobs" FOR UPDATE USING (("user_id" = "auth"."uid"()));



CREATE POLICY "pipeline_adj_user_policy" ON "public"."pipeline_adjustments" USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."pipeline_adjustments" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."pipeline_metrics" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."pipeline_runs" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "pipeline_runs_user_select" ON "public"."pipeline_runs" FOR SELECT USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."pipeline_tasks" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "prompt_ver_user_policy" ON "public"."prompt_versions" USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."prompt_versions" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."runs" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "runs_delete" ON "public"."runs" FOR DELETE USING (("user_id" = "auth"."uid"()));



CREATE POLICY "runs_insert" ON "public"."runs" FOR INSERT WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "runs_select" ON "public"."runs" FOR SELECT USING (("user_id" = "auth"."uid"()));



CREATE POLICY "runs_update" ON "public"."runs" FOR UPDATE USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."seen_jobs" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "seen_jobs_user_policy" ON "public"."seen_jobs" USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."self_improvement_config" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."user_resumes" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "user_resumes_delete" ON "public"."user_resumes" FOR DELETE USING (("user_id" = "auth"."uid"()));



CREATE POLICY "user_resumes_insert" ON "public"."user_resumes" FOR INSERT WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "user_resumes_select" ON "public"."user_resumes" FOR SELECT USING (("user_id" = "auth"."uid"()));



CREATE POLICY "user_resumes_update" ON "public"."user_resumes" FOR UPDATE USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."user_search_configs" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "user_search_configs_delete" ON "public"."user_search_configs" FOR DELETE USING (("user_id" = "auth"."uid"()));



CREATE POLICY "user_search_configs_insert" ON "public"."user_search_configs" FOR INSERT WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "user_search_configs_select" ON "public"."user_search_configs" FOR SELECT USING (("user_id" = "auth"."uid"()));



CREATE POLICY "user_search_configs_update" ON "public"."user_search_configs" FOR UPDATE USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."users" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "users_delete" ON "public"."users" FOR DELETE USING (("id" = "auth"."uid"()));



CREATE POLICY "users_insert" ON "public"."users" FOR INSERT WITH CHECK (("id" = "auth"."uid"()));



CREATE POLICY "users_select" ON "public"."users" FOR SELECT USING (("id" = "auth"."uid"()));



CREATE POLICY "users_update" ON "public"."users" FOR UPDATE USING (("id" = "auth"."uid"()));



GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";



GRANT ALL ON FUNCTION "public"."update_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_updated_at"() TO "service_role";



GRANT ALL ON TABLE "public"."ai_cache" TO "anon";
GRANT ALL ON TABLE "public"."ai_cache" TO "authenticated";
GRANT ALL ON TABLE "public"."ai_cache" TO "service_role";



GRANT ALL ON TABLE "public"."audit_log" TO "anon";
GRANT ALL ON TABLE "public"."audit_log" TO "authenticated";
GRANT ALL ON TABLE "public"."audit_log" TO "service_role";



GRANT ALL ON TABLE "public"."jobs" TO "anon";
GRANT ALL ON TABLE "public"."jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."jobs" TO "service_role";



GRANT ALL ON TABLE "public"."jobs_raw" TO "anon";
GRANT ALL ON TABLE "public"."jobs_raw" TO "authenticated";
GRANT ALL ON TABLE "public"."jobs_raw" TO "service_role";



GRANT ALL ON TABLE "public"."pipeline_adjustments" TO "anon";
GRANT ALL ON TABLE "public"."pipeline_adjustments" TO "authenticated";
GRANT ALL ON TABLE "public"."pipeline_adjustments" TO "service_role";



GRANT ALL ON TABLE "public"."pipeline_metrics" TO "anon";
GRANT ALL ON TABLE "public"."pipeline_metrics" TO "authenticated";
GRANT ALL ON TABLE "public"."pipeline_metrics" TO "service_role";



GRANT ALL ON TABLE "public"."pipeline_runs" TO "anon";
GRANT ALL ON TABLE "public"."pipeline_runs" TO "authenticated";
GRANT ALL ON TABLE "public"."pipeline_runs" TO "service_role";



GRANT ALL ON TABLE "public"."pipeline_tasks" TO "anon";
GRANT ALL ON TABLE "public"."pipeline_tasks" TO "authenticated";
GRANT ALL ON TABLE "public"."pipeline_tasks" TO "service_role";



GRANT ALL ON TABLE "public"."prompt_versions" TO "anon";
GRANT ALL ON TABLE "public"."prompt_versions" TO "authenticated";
GRANT ALL ON TABLE "public"."prompt_versions" TO "service_role";



GRANT ALL ON TABLE "public"."runs" TO "anon";
GRANT ALL ON TABLE "public"."runs" TO "authenticated";
GRANT ALL ON TABLE "public"."runs" TO "service_role";



GRANT ALL ON TABLE "public"."scrape_runs" TO "anon";
GRANT ALL ON TABLE "public"."scrape_runs" TO "authenticated";
GRANT ALL ON TABLE "public"."scrape_runs" TO "service_role";



GRANT ALL ON TABLE "public"."seen_jobs" TO "anon";
GRANT ALL ON TABLE "public"."seen_jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."seen_jobs" TO "service_role";



GRANT ALL ON TABLE "public"."self_improvement_config" TO "anon";
GRANT ALL ON TABLE "public"."self_improvement_config" TO "authenticated";
GRANT ALL ON TABLE "public"."self_improvement_config" TO "service_role";



GRANT ALL ON TABLE "public"."user_resumes" TO "anon";
GRANT ALL ON TABLE "public"."user_resumes" TO "authenticated";
GRANT ALL ON TABLE "public"."user_resumes" TO "service_role";



GRANT ALL ON TABLE "public"."user_search_configs" TO "anon";
GRANT ALL ON TABLE "public"."user_search_configs" TO "authenticated";
GRANT ALL ON TABLE "public"."user_search_configs" TO "service_role";



GRANT ALL ON TABLE "public"."users" TO "anon";
GRANT ALL ON TABLE "public"."users" TO "authenticated";
GRANT ALL ON TABLE "public"."users" TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";







