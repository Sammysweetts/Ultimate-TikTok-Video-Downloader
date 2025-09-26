import streamlit as st
import yt_dlp
import os
import re
import zipfile
import io
import json
import tempfile
import streamlit.components.v1 as components
from yt_dlp.utils import DownloadError

st.set_page_config(page_title="Ultimate TikTok Downloader", layout="wide")


# ==============================================================================
# 1. SHARED HELPER FUNCTIONS (COMMON TO ALL APPS)
#    These functions are defined once here to be used by all app versions.
# ==============================================================================

def sanitize_filename(name):
    """Removes invalid characters from a string to make it a valid filename."""
    if not name:
        return "untitled_video"
    sanitized_name = str(name)
    sanitized_name = re.sub(r'[\s\n\r]+', '_', sanitized_name)  # Replace whitespace with underscores
    sanitized_name = re.sub(r'[\\/*?:"<>|]', "", sanitized_name)  # Remove illegal characters
    return (sanitized_name[:100] + '..') if len(sanitized_name) > 100 else sanitized_name

def render_copy_link_button(url: str, key: str):
    """Renders a very small copy button that copies the given URL to the clipboard."""
    safe_url = json.dumps(url)
    btn_id = f"copy_btn_{sanitize_filename(str(key))}"
    html_code = f"""
    <div style="display:flex; align-items:center; justify-content:flex-end;">
      <button id="{btn_id}"
        title="Copy link"
        style="
          cursor:pointer; border:1px solid #93c5fd; border-radius:6px; background:#eaf2ff;
          padding:2px 6px; font-size:12px; line-height:1.1; color:#1e3a8a; margin-top: 5px;
        "
        aria-label="Copy link"
      >📋</button>
    </div>
    <script>
      (function() {{
        const btn = document.getElementById("{btn_id}");
        const text = {safe_url};
        if (btn) {{
          btn.addEventListener('click', async () => {{
            try {{
              await navigator.clipboard.writeText(text);
              const oldText = btn.textContent; btn.textContent = "✓";
              btn.style.background = "#e7f9ee"; btn.style.borderColor = "#16a34a";
              setTimeout(() => {{ btn.textContent = oldText; btn.style.background = "#eaf2ff"; btn.style.borderColor = "#93c5fd"; }}, 1200);
            }} catch (err) {{
              const oldText = btn.textContent; btn.textContent = "!";
              btn.style.background = "#fdecec"; btn.style.borderColor = "#ef4444";
              setTimeout(() => {{ btn.textContent = oldText; btn.style.background = "#eaf2ff"; btn.style.borderColor = "#93c5fd"; }}, 1200);
            }}
          }});
        }}
      }})();
    </script>
    """
    components.html(html_code, height=35, width=50, scrolling=False)

def fetch_user_videos(username, limit=None):
    """Fetches a list of video metadata from a TikTok user's profile."""
    profile_url = f"https://www.tiktok.com/@{username}"
    ydl_opts = {'quiet': True, 'extract_flat': True, 'force_generic_extractor': True, 'skip_download': True}
    if limit: ydl_opts['playlistend'] = limit
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(profile_url, download=False)
            if 'entries' in info and info['entries']:
                video_list, total_videos = [], len(info['entries'])
                st.write(f"Found {total_videos} videos. Fetching individual details...")
                progress_bar = st.progress(0, text="Fetching video details...")
                for i, entry in enumerate(info['entries']):
                    try:
                        with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}) as ydl_single:
                            video_info = ydl_single.extract_info(entry['url'], download=False)
                            video_list.append(video_info)
                        progress_bar.progress((i + 1) / total_videos, text=f"Fetching video {i+1}/{total_videos}")
                    except Exception as single_e:
                        print(f"Could not fetch metadata for entry {entry.get('url')}: {single_e}")
                        continue
                progress_bar.empty()
                return video_list, None
            else: return None, "No videos found for this user, or the profile is private."
    except DownloadError as e:
        if "HTTP Error 404" in str(e): return None, f"❌ User '{username}' not found. Please check the User ID."
        return None, f"❌ Invalid User ID or private profile. Error: {e}"
    except Exception as e: return None, f"An unexpected error occurred: {e}"

def robust_download_to_memory(video_info):
    """
    The most robust download function. Downloads any TikTok content (video, slideshows)
    to a temporary file and then reads it into memory. This is the core download logic.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            uploader = sanitize_filename(video_info.get('uploader', 'user'))
            title = sanitize_filename(video_info.get('title', 'video'))
            final_filename = f"{uploader}_{title}.mp4"
            filename_template = f"{video_info.get('id', 'video')}.%(ext)s"

            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': os.path.join(temp_dir, filename_template),
                'quiet': True,
                'merge_output_format': 'mp4',
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_info['webpage_url']])

            downloaded_files = os.listdir(temp_dir)
            if not downloaded_files: return None, None

            downloaded_file_path = os.path.join(temp_dir, downloaded_files[0])
            with open(downloaded_file_path, 'rb') as f:
                video_bytes = f.read()
            
            return video_bytes, final_filename
        except Exception as e:
            print(f"Failed to download {video_info.get('webpage_url')}. Error: {e}")
            return None, None

# Cached version of the download function for App 3
@st.cache_data(show_spinner=False)
def get_cached_download_data(video_info):
    return robust_download_to_memory(video_info)

def chunked(iterable, size):
    """Yields successive n-sized chunks from an iterable."""
    for i in range(0, len(iterable), size):
        yield iterable[i:i + size]

def initialize_session_state():
    """Resets the session state, useful when switching between apps."""
    state_defaults = {
        'video_list': [], 'select_all': False, 'select_all_bottom': False,
        'zip_bytes': None, 'zipped_selection_ids': [], 'zip_filename': None, 'failed_videos': [],
        'preparing_video_id': None, 'prepared_download': {},
        'user_id': "",
        'download_triggered': False,
    }
    for key, default in state_defaults.items():
        st.session_state[key] = default

def on_download_click():
    """Callback function to set a flag when the download button is clicked."""
    st.session_state.download_triggered = True

def common_action_bar_ui(position="top"):
    """Renders the common UI for selecting and preparing ZIP files."""
    selected_videos = [v for v in st.session_state.video_list if st.session_state.get(v['id'])]
    st.info(f"You have selected **{len(selected_videos)}** video(s).")
    
    col1, col2, col3 = st.columns([3, 2, 2])
    
    with col1:
        st.checkbox("Select All / Deselect All", key=f'select_all_{position}', on_change=toggle_all_selection, args=(position,))
    
    with col2:
        if st.button(f"⬇️ Prepare {len(selected_videos)} Videos for ZIP", key=f"prepare_zip_{position}", use_container_width=True, disabled=len(selected_videos) == 0):
            prepare_zip(selected_videos, st.session_state.user_id)
            
    with col3:
        if st.session_state.zip_bytes:
            st.download_button(
                "📦 Download Zip Folder", 
                st.session_state.zip_bytes, 
                st.session_state.zip_filename, 
                "application/zip", 
                use_container_width=True, 
                key=f"download_zip_{position}",
                on_click=on_download_click
            )
            
    if st.session_state.get('download_triggered'):
        st.toast("Your download is starting! Please check your browser.", icon="✅")
        st.session_state.download_triggered = False # Reset the flag

def toggle_all_selection(position):
    """Callback for 'Select All' checkboxes."""
    key = f'select_all_{position}'
    new_state = st.session_state[key]
    for video in st.session_state.video_list:
        st.session_state[video['id']] = new_state
    invalidate_zip()

def invalidate_zip():
    """Clears any previously generated ZIP file from state."""
    st.session_state.zip_bytes = None
    st.session_state.zipped_selection_ids = []

def prepare_zip(selected_videos, user_id):
    """Downloads selected videos and creates a zip file in memory."""
    if not selected_videos:
        st.warning("No videos selected.")
        return

    failed_videos_this_run = []
    with st.spinner("Downloading and zipping videos... Please wait."):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            download_progress = st.progress(0, text="Starting download...")
            total_to_download = len(selected_videos)
            for idx, video in enumerate(selected_videos):
                title = video.get('title', 'video')[:30]
                download_progress.progress((idx + 1) / total_to_download, text=f"Zipping '{title}...'")
                
                video_bytes, filename = robust_download_to_memory(video)

                if video_bytes and filename:
                    zip_file.writestr(filename, video_bytes)
                else:
                    failed_videos_this_run.append(video)
        download_progress.empty()

    st.session_state.failed_videos = failed_videos_this_run
    
    if failed_videos_this_run:
        st.warning(f"Zip process complete, but {len(failed_videos_this_run)} video(s) were skipped.")
    else:
        st.success("Zip is ready! Use the 'Download Zip Folder' button to save.")

    if zip_buffer.tell() > 0:
        st.session_state.zip_bytes = zip_buffer.getvalue()
        successful_ids = [v['id'] for v in selected_videos if v not in failed_videos_this_run]
        st.session_state.zipped_selection_ids = successful_ids
        st.session_state.zip_filename = f"tiktok_download_{user_id}.zip"
    else:
        invalidate_zip()

def display_failed_videos():
    """Shows a list of videos that failed to download."""
    if st.session_state.failed_videos:
        st.subheader("⚠️ Skipped Videos")
        st.write("The following videos could not be downloaded. You can copy their links to try manually.")
        for failed_video in st.session_state.failed_videos:
            fcol1, fcol2 = st.columns([5, 1])
            with fcol1:
                st.warning(f"Could not download: **{failed_video.get('title', 'Unknown Video')}**")
            with fcol2:
                render_copy_link_button(failed_video.get('webpage_url'), f"failed_copy_{failed_video.get('id')}")


# ==============================================================================
# 2. APP-SPECIFIC LOGIC AND UI
#    Each function here represents one of the original code versions.
# ==============================================================================

# --- APP 1: Simple Bulk Downloader ---
def run_app_1():
    st.header("App 1: Simple Bulk Downloader")
    st.write("This version focuses on a clean, straightforward bulk download experience. Select videos and prepare a single ZIP file.")
    
    if not st.session_state.get('download_triggered', False):
        selected_ids = {v['id'] for v in st.session_state.video_list if st.session_state.get(v['id'])}
        if st.session_state.zip_bytes and selected_ids != set(st.session_state.zipped_selection_ids):
            invalidate_zip()
        
    common_action_bar_ui(position="top")
    display_failed_videos()
    st.divider()

    # Video Grid
    num_columns = 5
    for row_videos in chunked(st.session_state.video_list, num_columns):
        cols = st.columns(num_columns)
        for idx, video in enumerate(row_videos):
            with cols[idx]:
                st.image(video.get('thumbnail'), use_container_width=True)
                tcol, bcol = st.columns([6, 1])
                with tcol: st.toggle(f"❤️{video.get('like_count', 0):,}", key=video.get('id'), help="Select for ZIP download")
                with bcol:
                    vid_url = video.get('webpage_url') or video.get('url')
                    if vid_url: render_copy_link_button(vid_url, key=f"{video.get('id')}_copy")
        st.markdown("---")
        
    st.divider()
    common_action_bar_ui(position="bottom")


# --- APP 2: Bulk + Interactive Single Download ---
def run_app_2():
    st.header("App 2: Bulk + Interactive Single Download")
    st.write("This version provides both bulk ZIP functionality and an interactive 'Prepare & Download' button for each individual video.")
    
    def prepare_single_download_callback(video):
        st.session_state.prepared_download = {}
        st.session_state.preparing_video_id = video['id']
        video_bytes, filename = robust_download_to_memory(video)
        if video_bytes and filename:
            st.session_state.prepared_download = {'id': video['id'], 'data': video_bytes, 'filename': filename}
        else:
            st.session_state.toast_error = f"Failed to prepare '{video.get('title', 'video')[:30]}...'"
        st.session_state.preparing_video_id = None
        
    if not st.session_state.get('download_triggered', False):
        selected_ids = {v['id'] for v in st.session_state.video_list if st.session_state.get(v['id'])}
        if st.session_state.zip_bytes and selected_ids != set(st.session_state.zipped_selection_ids):
            invalidate_zip()

    common_action_bar_ui(position="top")
    display_failed_videos()
    st.divider()

    st.subheader("Individual Videos")
    if 'toast_error' in st.session_state:
        st.toast(st.session_state.toast_error, icon="❌")
        del st.session_state.toast_error 
        
    num_columns = 5
    for row_videos in chunked(st.session_state.video_list, num_columns):
        cols = st.columns(num_columns)
        for idx, video in enumerate(row_videos):
            video_id = video.get('id')
            with cols[idx]:
                st.image(video.get('thumbnail'), use_container_width=True, caption=f"❤️{video.get('like_count', 0):,}")
                tcol, ccol = st.columns([4, 1])
                with tcol: st.toggle("Select for ZIP", key=video_id)
                with ccol: render_copy_link_button(video.get('webpage_url'), f"{video_id}_copy")
                
                is_ready = st.session_state.prepared_download.get('id') == video_id
                is_preparing = st.session_state.preparing_video_id == video_id
                
                if is_ready:
                    st.download_button("✅ Click to Save", st.session_state.prepared_download['data'], st.session_state.prepared_download['filename'], "video/mp4", use_container_width=True, on_click=lambda: st.session_state.prepared_download.clear())
                elif is_preparing:
                    st.button("⏳ Preparing...", use_container_width=True, disabled=True)
                else:
                    st.button("⬇️ Download Video", f"prepare_{video_id}", use_container_width=True, on_click=prepare_single_download_callback, args=(video,), disabled=st.session_state.preparing_video_id is not None)
        st.markdown("<div style='height: 1.5rem;'></div>", unsafe_allow_html=True)

    st.divider()
    common_action_bar_ui(position="bottom")


# --- APP 3: Bulk + Pre-Cached Direct Download ---
def run_app_3():
    st.header("App 3: Bulk + Pre-Cached Direct Download")
    st.write("This version pre-fetches and caches all individual download links. The initial load may be slower, but clicking to download each video is instant. Good for downloading a few specific videos quickly.")
    
    if not st.session_state.get('download_triggered', False):
        selected_ids = {v['id'] for v in st.session_state.video_list if st.session_state.get(v['id'])}
        if st.session_state.zip_bytes and selected_ids != set(st.session_state.zipped_selection_ids):
            invalidate_zip()

    common_action_bar_ui(position="top")
    display_failed_videos()
    st.divider()
    
    num_columns = 5
    with st.spinner("Preparing direct download links for all videos... (This may take a moment)"):
        for row_videos in chunked(st.session_state.video_list, num_columns):
            cols = st.columns(num_columns)
            for idx, video in enumerate(row_videos):
                video_id = video.get('id')
                with cols[idx]:
                    st.image(video.get('thumbnail'), use_container_width=True)
                    video_bytes, filename = get_cached_download_data(video)
                    
                    tgl_col, cpy_col, dl_col = st.columns([5, 2, 2])
                    with tgl_col: st.toggle(f"❤️ {video.get('like_count', 0):,}", key=video_id, help="Select for bulk .zip")
                    with cpy_col: render_copy_link_button(video.get('webpage_url'), f"{video_id}_copy")
                    with dl_col:
                        if video_bytes and filename:
                            st.download_button("⬇️", video_bytes, filename, "video/mp4", key=f"dl_{video_id}", help=f"Download '{filename}'")
                        else:
                            st.button("❌", key=f"dl_err_{video_id}", disabled=True, help="Download unavailable")
            st.write("")

    st.divider()
    common_action_bar_ui(position="bottom")


# ==============================================================================
# 2.5. BUTTON COLOR INJECTION (STYLE ONLY, NO LOGIC CHANGES)
# ==============================================================================

def inject_button_colorizer():
    """Injects CSS and JS to color-code different buttons by their label text."""
    components.html("""
    <script>
      (function () {
        try {
          if (parent.window.__BTN_COLORIZER_LOADED__) return;
          parent.window.__BTN_COLORIZER_LOADED__ = true;

          const CSS = `
            .btn-blue, .btn-blue:focus { background-color:#2563eb !important; border-color:#1e40af !important; color:#ffffff !important; }
            .btn-blue:hover { background-color:#1d4ed8 !important; }
            .btn-blue:active { background-color:#16a34a !important; border-color:#15803d !important; } /* Flash Green on click */

            .btn-green, .btn-green:focus, .btn-green:active { background-color:#16a34a !important; border-color:#15803d !important; color:#ffffff !important; }
            .btn-green:hover { background-color:#15803d !important; }

            .btn-orange, .btn-orange:focus, .btn-orange:active { background-color:#f59e0b !important; border-color:#d97706 !important; color:#ffffff !important; }
            .btn-orange:hover { background-color:#d97706 !important; }

            .btn-red, .btn-red:focus, .btn-red:active { background-color:#ef4444 !important; border-color:#dc2626 !important; color:#ffffff !important; }
            .btn-red:hover { background-color:#dc2626 !important; }

            .btn-gray, .btn-gray:focus, .btn-gray:active { background-color:#6b7280 !important; border-color:#4b5563 !important; color:#ffffff !important; }
            .btn-gray:hover { background-color:#4b5563 !important; }

            button, a[role="button"] { border-radius:8px !important; transition: background-color 0.1s ease-in-out; }
          `;

          const doc = parent.document;
          const styleId = "btn-color-classes";
          if (!doc.getElementById(styleId)) {
            const styleEl = doc.createElement("style");
            styleEl.id = styleId;
            styleEl.type = "text/css";
            styleEl.appendChild(doc.createTextNode(CSS));
            doc.head.appendChild(styleEl);
          }

          const assignColors = () => {
            const nodes = Array.from(doc.querySelectorAll('button, a[role="button"], div[data-testid="stDownloadButton"] a'));
            nodes.forEach(btn => {
              const t = (btn.innerText || btn.textContent || "").toLowerCase().trim();
              btn.classList.remove("btn-blue","btn-green","btn-orange","btn-red","btn-gray");

              if (t.includes("prepare")) {
                btn.classList.add("btn-orange");
              } else if (t.includes("download zip folder") || t.includes("click to save")) {
                btn.classList.add("btn-green");
              } else if (t.includes("download video") || t === "⬇️") {
                btn.classList.add("btn-blue");
              } else if (t.includes("restart application") || t === "❌") {
                btn.classList.add("btn-red");
              } else if (t.includes("launch pre-cached downloader")) {
                btn.classList.add("btn-green");
              } else if (t.includes("launch interactive downloader")) {
                btn.classList.add("btn-orange");
              } else if (t.includes("launch simple bulk downloader")) {
                btn.classList.add("btn-blue");
              } else if (t.includes("fetch videos") || t.includes("clear download cache")) {
                btn.classList.add("btn-blue");
              } else if (t.includes("change app version") || t.includes("preparing")) {
                btn.classList.add("btn-gray");
              }
            });
          };

          // Initial assignment
          assignColors();

          // Observe app for rerenders and reapply
          const root = doc.querySelector('div[data-testid="stAppViewContainer"]') || doc.body;
          const obs = new parent.MutationObserver(() => assignColors());
          obs.observe(root, { childList: true, subtree: true });
        } catch (e) {
          // no-op
        }
      })();
    </script>
    """, height=0, width=0)


# ==============================================================================
# 3. MAIN APP ROUTER
#    This is the main entry point that controls which page is displayed.
# ==============================================================================

def main():
    st.title("🎬 Ultimate TikTok Video Downloader")

    # Inject color styles (purely visual)
    inject_button_colorizer()

    st.sidebar.title("⚙️ App Controls")
    st.sidebar.write("Use these buttons to manage the app's state and cache.")

    if st.sidebar.button("🧹 Clear Download Cache", help="Clears the cache for downloaded video data (primarily used in App 3)."):
        st.cache_data.clear()
        st.toast("Cache cleared successfully!", icon="✅")

    if st.sidebar.button("🔄 Restart Application", help="Clears all session data and returns to the app selection screen."):
        keys = list(st.session_state.keys())
        for key in keys:
            del st.session_state[key]
        st.rerun()

    if 'app_choice' not in st.session_state:
        st.subheader("Choose an Experience")
        st.write("Select one of the downloader versions below. Each offers a slightly different workflow.")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.info("App 1: Simple Bulk", icon="📦")
            st.write("A clean interface focused purely on selecting multiple videos and downloading them in a single ZIP file.")
            if st.button("Launch Simple Bulk Downloader", use_container_width=True):
                st.session_state.app_choice = 'app1'
                initialize_session_state()
                st.rerun()
        
        with col2:
            st.info("App 2: Bulk + Interactive Single", icon="⏯️")
            st.write("Download in bulk via ZIP, or prepare and download videos one-by-one with an interactive button.")
            if st.button("Launch Interactive Downloader", use_container_width=True):
                st.session_state.app_choice = 'app2'
                initialize_session_state()
                st.rerun()
                
        with col3:
            st.info("App 3: Bulk + Pre-Cached Direct", icon="⚡")
            st.write("Pre-fetches all individual downloads. Slower initial load, but instant single-click downloads for each video.")
            if st.button("Launch Pre-Cached Downloader", use_container_width=True):
                st.session_state.app_choice = 'app3'
                initialize_session_state()
                st.rerun()
        return

    if st.button("← Change App Version"):
        del st.session_state.app_choice
        initialize_session_state()
        st.rerun()

    st.session_state.user_id = st.text_input(
        "Enter TikTok User ID:", 
        placeholder="e.g., khaby.lame", 
        key='user_id_input'
    )
    st.markdown("#### Fetch Options")
    c1, c2 = st.columns([1, 3])
    with c1: fetch_all = st.checkbox("Fetch all videos", help="WARNING: Can be very slow for large profiles!")
    with c2: video_limit = st.number_input("Max videos to fetch", 1, 2000, 50, disabled=fetch_all)

    if st.button("🔍 Fetch Videos", use_container_width=True):
        initialize_session_state()
        st.session_state.user_id = st.session_state.user_id_input
        if st.session_state.user_id:
            with st.spinner(f"Fetching videos from '{st.session_state.user_id}'..."):
                videos, error_msg = fetch_user_videos(st.session_state.user_id, limit=None if fetch_all else video_limit)
                if error_msg:
                    st.error(error_msg)
                else:
                    st.success(f"Successfully fetched details for {len(videos)} videos!")
                    st.session_state.video_list = videos
                    for video in videos: st.session_state[video['id']] = False
        else:
            st.warning("Please enter a User ID.")

    if st.session_state.video_list:
        st.divider()
        if st.session_state.app_choice == 'app1':
            run_app_1()
        elif st.session_state.app_choice == 'app2':
            run_app_2()
        elif st.session_state.app_choice == 'app3':
            run_app_3()


if __name__ == "__main__":
    main()
