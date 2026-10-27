--
-- PostgreSQL database dump
--

\restrict AtTz796Zt6VmpH48w0j4NM4cgS3V4MRg1E7bHBmdB2AxW7nWPqOUES2y8abWctX

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

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

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: unaccent; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;


--
-- Name: EXTENSION unaccent; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION unaccent IS 'text search dictionary that removes accents';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: authors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.authors (
    id bigint NOT NULL,
    name text NOT NULL
);


--
-- Name: authors_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.authors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: authors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.authors_id_seq OWNED BY public.authors.id;


--
-- Name: book_authors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.book_authors (
    book_id bigint NOT NULL,
    author_id bigint NOT NULL
);


--
-- Name: book_languages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.book_languages (
    book_id bigint NOT NULL,
    language_id bigint NOT NULL
);


--
-- Name: book_publishers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.book_publishers (
    book_id bigint NOT NULL,
    publisher_id bigint NOT NULL
);


--
-- Name: book_series; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.book_series (
    book_id bigint NOT NULL,
    series_id bigint NOT NULL
);


--
-- Name: book_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.book_tags (
    book_id bigint NOT NULL,
    tag_id bigint NOT NULL
);


--
-- Name: books; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.books (
    id bigint NOT NULL,
    title text NOT NULL,
    sort text,
    "timestamp" timestamp without time zone,
    pubdate timestamp without time zone,
    series_index numeric,
    author_sort text,
    isbn text,
    lccn text,
    path text NOT NULL,
    flags integer,
    uuid text,
    last_modified timestamp without time zone,
    has_cover integer
);


--
-- Name: books_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.books_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: books_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.books_id_seq OWNED BY public.books.id;


--
-- Name: comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comments (
    book_id bigint NOT NULL,
    text text
);


--
-- Name: data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data (
    id bigint NOT NULL,
    book_id bigint NOT NULL,
    format text NOT NULL,
    uncompressed_size bigint NOT NULL,
    name text NOT NULL
);


--
-- Name: data_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.data_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: data_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.data_id_seq OWNED BY public.data.id;


--
-- Name: favorites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.favorites (
    user_id integer NOT NULL,
    book_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: identifiers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.identifiers (
    id bigint NOT NULL,
    book_id bigint NOT NULL,
    type text NOT NULL,
    val text NOT NULL
);


--
-- Name: identifiers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.identifiers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: identifiers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.identifiers_id_seq OWNED BY public.identifiers.id;


--
-- Name: languages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.languages (
    id bigint NOT NULL,
    lang_code text
);


--
-- Name: languages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.languages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: languages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.languages_id_seq OWNED BY public.languages.id;


--
-- Name: login_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_attempts (
    id integer NOT NULL,
    ip_address character varying(64) NOT NULL,
    username character varying(100),
    attempted_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: login_attempts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.login_attempts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: login_attempts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.login_attempts_id_seq OWNED BY public.login_attempts.id;


--
-- Name: publishers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.publishers (
    id bigint NOT NULL,
    name text NOT NULL
);


--
-- Name: publishers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.publishers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: publishers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.publishers_id_seq OWNED BY public.publishers.id;


--
-- Name: reading_progress; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reading_progress (
    user_id integer NOT NULL,
    book_id integer NOT NULL,
    cfi text NOT NULL,
    percentage real DEFAULT 0.0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: reading_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reading_status (
    user_id integer NOT NULL,
    book_id integer NOT NULL,
    status character varying(20) NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: series; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.series (
    id bigint NOT NULL,
    name text NOT NULL
);


--
-- Name: series_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.series_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: series_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.series_id_seq OWNED BY public.series.id;


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    session_id character varying(128) NOT NULL,
    user_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tags (
    id bigint NOT NULL,
    name text NOT NULL
);


--
-- Name: tags_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tags_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tags_id_seq OWNED BY public.tags.id;


--
-- Name: user_bookmarks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_bookmarks (
    id integer NOT NULL,
    user_id integer NOT NULL,
    book_id bigint NOT NULL,
    cfi text NOT NULL,
    label text,
    percentage integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: user_bookmarks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_bookmarks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_bookmarks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_bookmarks_id_seq OWNED BY public.user_bookmarks.id;


--
-- Name: user_notes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_notes (
    id integer NOT NULL,
    user_id integer NOT NULL,
    book_id bigint NOT NULL,
    cfi text NOT NULL,
    text text,
    note text,
    color character varying(20) DEFAULT 'yellow'::character varying,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: user_notes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_notes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_notes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_notes_id_seq OWNED BY public.user_notes.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(100) NOT NULL,
    email character varying(255),
    password_hash character varying(255) NOT NULL,
    role character varying(20) DEFAULT 'user'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    last_login timestamp with time zone,
    kindle_email character varying(255)
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: authors id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authors ALTER COLUMN id SET DEFAULT nextval('public.authors_id_seq'::regclass);


--
-- Name: books id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.books ALTER COLUMN id SET DEFAULT nextval('public.books_id_seq'::regclass);


--
-- Name: data id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data ALTER COLUMN id SET DEFAULT nextval('public.data_id_seq'::regclass);


--
-- Name: identifiers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identifiers ALTER COLUMN id SET DEFAULT nextval('public.identifiers_id_seq'::regclass);


--
-- Name: languages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.languages ALTER COLUMN id SET DEFAULT nextval('public.languages_id_seq'::regclass);


--
-- Name: login_attempts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_attempts ALTER COLUMN id SET DEFAULT nextval('public.login_attempts_id_seq'::regclass);


--
-- Name: publishers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publishers ALTER COLUMN id SET DEFAULT nextval('public.publishers_id_seq'::regclass);


--
-- Name: series id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.series ALTER COLUMN id SET DEFAULT nextval('public.series_id_seq'::regclass);


--
-- Name: tags id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags ALTER COLUMN id SET DEFAULT nextval('public.tags_id_seq'::regclass);


--
-- Name: user_bookmarks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_bookmarks ALTER COLUMN id SET DEFAULT nextval('public.user_bookmarks_id_seq'::regclass);


--
-- Name: user_notes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_notes ALTER COLUMN id SET DEFAULT nextval('public.user_notes_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: authors authors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authors
    ADD CONSTRAINT authors_pkey PRIMARY KEY (id);


--
-- Name: book_authors book_authors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_authors
    ADD CONSTRAINT book_authors_pkey PRIMARY KEY (book_id, author_id);


--
-- Name: book_languages book_languages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_languages
    ADD CONSTRAINT book_languages_pkey PRIMARY KEY (book_id, language_id);


--
-- Name: book_publishers book_publishers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_publishers
    ADD CONSTRAINT book_publishers_pkey PRIMARY KEY (book_id, publisher_id);


--
-- Name: book_series book_series_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_series
    ADD CONSTRAINT book_series_pkey PRIMARY KEY (book_id, series_id);


--
-- Name: book_tags book_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_tags
    ADD CONSTRAINT book_tags_pkey PRIMARY KEY (book_id, tag_id);


--
-- Name: books books_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.books
    ADD CONSTRAINT books_pkey PRIMARY KEY (id);


--
-- Name: comments comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_pkey PRIMARY KEY (book_id);


--
-- Name: data data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data
    ADD CONSTRAINT data_pkey PRIMARY KEY (id);


--
-- Name: favorites favorites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorites
    ADD CONSTRAINT favorites_pkey PRIMARY KEY (user_id, book_id);


--
-- Name: identifiers identifiers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.identifiers
    ADD CONSTRAINT identifiers_pkey PRIMARY KEY (id);


--
-- Name: languages languages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.languages
    ADD CONSTRAINT languages_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX idx_languages_lang_code ON public.languages USING btree (lang_code);


--
-- Name: login_attempts login_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_attempts
    ADD CONSTRAINT login_attempts_pkey PRIMARY KEY (id);


--
-- Name: publishers publishers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publishers
    ADD CONSTRAINT publishers_pkey PRIMARY KEY (id);


--
-- Name: reading_progress reading_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reading_progress
    ADD CONSTRAINT reading_progress_pkey PRIMARY KEY (user_id, book_id);


--
-- Name: reading_status reading_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reading_status
    ADD CONSTRAINT reading_status_pkey PRIMARY KEY (user_id, book_id);


--
-- Name: series series_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.series
    ADD CONSTRAINT series_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (session_id);


--
-- Name: tags tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_pkey PRIMARY KEY (id);


--
-- Name: user_bookmarks user_bookmarks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_bookmarks
    ADD CONSTRAINT user_bookmarks_pkey PRIMARY KEY (id);


--
-- Name: user_notes user_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_notes
    ADD CONSTRAINT user_notes_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: idx_authors_lower_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_authors_lower_name ON public.authors USING btree (lower(name));


--
-- Name: idx_authors_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_authors_name ON public.authors USING gin (name public.gin_trgm_ops);


--
-- Name: idx_book_authors_author; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_authors_author ON public.book_authors USING btree (author_id);


--
-- Name: idx_book_authors_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_authors_book ON public.book_authors USING btree (book_id);


--
-- Name: idx_book_languages_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_languages_book ON public.book_languages USING btree (book_id);


--
-- Name: idx_book_languages_language; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_languages_language ON public.book_languages USING btree (language_id);


--
-- Name: idx_book_publishers_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_publishers_book ON public.book_publishers USING btree (book_id);


--
-- Name: idx_book_publishers_publisher; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_publishers_publisher ON public.book_publishers USING btree (publisher_id);


--
-- Name: idx_book_series_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_series_book ON public.book_series USING btree (book_id);


--
-- Name: idx_book_series_series; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_series_series ON public.book_series USING btree (series_id);


--
-- Name: idx_book_tags_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_tags_book ON public.book_tags USING btree (book_id);


--
-- Name: idx_book_tags_tag; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_tags_tag ON public.book_tags USING btree (tag_id);


--
-- Name: idx_books_author_sort; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_author_sort ON public.books USING gin (author_sort public.gin_trgm_ops);


--
-- Name: idx_books_last_modified; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_last_modified ON public.books USING btree (last_modified);


--
-- Name: idx_books_path; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_path ON public.books USING btree (path);


--
-- Name: idx_books_pubdate; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_pubdate ON public.books USING btree (pubdate);


--
-- Name: idx_books_sort; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_sort ON public.books USING gin (sort public.gin_trgm_ops);


--
-- Name: idx_books_title; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_title ON public.books USING gin (title public.gin_trgm_ops);


--
-- Name: idx_books_title_author_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_title_author_hash ON public.books USING btree (md5(((lower(TRIM(BOTH FROM title)) || '|||'::text) || lower(TRIM(BOTH FROM author_sort)))));


--
-- Name: idx_books_title_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_title_hash ON public.books USING btree (md5(lower(TRIM(BOTH FROM title))));


--
-- Name: idx_books_unique_path; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_books_unique_path ON public.books USING btree (path) WHERE ((path IS NOT NULL) AND (path <> ''::text));


--
-- Name: idx_books_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_uuid ON public.books USING btree (uuid);


--
-- Name: idx_comments_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comments_book ON public.comments USING btree (book_id);


--
-- Name: idx_comments_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comments_fts ON public.comments USING gin (to_tsvector('simple'::regconfig, text));


--
-- Name: idx_comments_text; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comments_text ON public.comments USING gin (to_tsvector('simple'::regconfig, text));


--
-- Name: idx_data_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_data_book ON public.data USING btree (book_id);


--
-- Name: idx_data_format; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_data_format ON public.data USING btree (format);


--
-- Name: idx_favorites_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_favorites_book ON public.favorites USING btree (book_id);


--
-- Name: idx_favorites_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_favorites_user ON public.favorites USING btree (user_id);


--
-- Name: idx_identifiers_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_identifiers_book ON public.identifiers USING btree (book_id);


--
-- Name: idx_identifiers_type_val; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_identifiers_type_val ON public.identifiers USING btree (type, val);


--
-- Name: idx_login_attempts_ip; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_login_attempts_ip ON public.login_attempts USING btree (ip_address, attempted_at);


--
-- Name: idx_login_attempts_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_login_attempts_user ON public.login_attempts USING btree (lower((username)::text), attempted_at);


--
-- Name: idx_publishers_lower_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_publishers_lower_name ON public.publishers USING btree (lower(name));


--
-- Name: idx_publishers_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_publishers_name ON public.publishers USING gin (name public.gin_trgm_ops);


--
-- Name: idx_reading_progress_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reading_progress_user ON public.reading_progress USING btree (user_id);


--
-- Name: idx_reading_status_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reading_status_user_status ON public.reading_status USING btree (user_id, status);


--
-- Name: idx_series_lower_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_series_lower_name ON public.series USING btree (lower(name));


--
-- Name: idx_series_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_series_name ON public.series USING gin (name public.gin_trgm_ops);


--
-- Name: idx_sessions_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_expires ON public.sessions USING btree (expires_at);


--
-- Name: idx_sessions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_user ON public.sessions USING btree (user_id);


--
-- Name: idx_tags_lower_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tags_lower_name ON public.tags USING btree (lower(name));


--
-- Name: idx_tags_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tags_name ON public.tags USING gin (name public.gin_trgm_ops);


--
-- Name: idx_user_bookmarks_user_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_bookmarks_user_book ON public.user_bookmarks USING btree (user_id, book_id);


--
-- Name: idx_user_notes_user_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_notes_user_book ON public.user_notes USING btree (user_id, book_id);


--
-- Name: favorites favorites_book_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorites
    ADD CONSTRAINT favorites_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;


--
-- Name: favorites favorites_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorites
    ADD CONSTRAINT favorites_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: reading_progress reading_progress_book_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reading_progress
    ADD CONSTRAINT reading_progress_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;


--
-- Name: reading_progress reading_progress_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reading_progress
    ADD CONSTRAINT reading_progress_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: reading_status reading_status_book_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reading_status
    ADD CONSTRAINT reading_status_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;


--
-- Name: reading_status reading_status_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reading_status
    ADD CONSTRAINT reading_status_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_bookmarks user_bookmarks_book_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_bookmarks
    ADD CONSTRAINT user_bookmarks_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;


--
-- Name: user_bookmarks user_bookmarks_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_bookmarks
    ADD CONSTRAINT user_bookmarks_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_notes user_notes_book_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_notes
    ADD CONSTRAINT user_notes_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;


--
-- Name: user_notes user_notes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_notes
    ADD CONSTRAINT user_notes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict AtTz796Zt6VmpH48w0j4NM4cgS3V4MRg1E7bHBmdB2AxW7nWPqOUES2y8abWctX

