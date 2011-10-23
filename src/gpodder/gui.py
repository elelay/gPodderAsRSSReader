# -*- coding: utf-8 -*-
#
# gPodder - A media aggregator and podcast client
# Copyright (c) 2005-2011 Thomas Perl and the gPodder Team
#
# gPodder is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# gPodder is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

SHOWNOTES_HTML_TEMPLATE = """
<html>
  <head>
    <meta http-equiv="content-type" content="text/html; charset=utf-8"/>
  </head>
  <body>
    <a href="%s" style="color:black;font-size: big; font-weight: bold;">%s</a>
    <br>
    <span style="font-size: small;">%s</span>
    <hr style="border: 1px #eeeeee solid;">
    <p>%s</p>
  </body>
</html>
"""


import os
import platform
import gtk
import gtk.gdk
import gobject
import pango
import random
import sys
import shutil
import subprocess
import glob
import time
import tempfile
import collections
import threading
import Queue
import urllib

from xml.sax import saxutils

import gpodder

try:
    import dbus
    import dbus.service
    import dbus.mainloop
    import dbus.glib
except ImportError:
    # Mock the required D-Bus interfaces with no-ops (ugly? maybe.)
    class dbus:
        class SessionBus:
            def __init__(self, *args, **kwargs):
                pass
            def add_signal_receiver(self, *args, **kwargs):
                pass
        class glib:
            class DBusGMainLoop:
                def __init__(self, *args, **kwargs):
                    pass
        class service:
            @staticmethod
            def method(*args, **kwargs):
                return lambda x: x
            class BusName:
                def __init__(self, *args, **kwargs):
                    pass
            class Object:
                def __init__(self, *args, **kwargs):
                    pass


from gpodder import feedcore
from gpodder import util
from gpodder import opml
from gpodder import download
from gpodder import my
from gpodder import youtube
from gpodder import player
from gpodder.liblogger import log

_ = gpodder.gettext
N_ = gpodder.ngettext

from gpodder.model import PodcastChannel
from gpodder.model import PodcastEpisode
from gpodder.dbsqlite import Database

from gpodder.gtkui.model import PodcastListModel
from gpodder.gtkui.model import EpisodeListModel
from gpodder.gtkui.config import UIConfig
from gpodder.gtkui.services import CoverDownloader
from gpodder.gtkui.widgets import SimpleMessageArea
from gpodder.gtkui.desktopfile import UserAppsReader

from gpodder.gtkui.draw import draw_text_box_centered

from gpodder.gtkui.interface.common import BuilderWidget
from gpodder.gtkui.interface.common import TreeViewHelper
from gpodder.gtkui.interface.addpodcast import gPodderAddPodcast

if gpodder.ui.desktop:
    from gpodder.gtkui.download import DownloadStatusModel

    from gpodder.gtkui.desktop.sync import gPodderSyncUI

    from gpodder.gtkui.desktop.channel import gPodderChannel
    from gpodder.gtkui.desktop.preferences import gPodderPreferences
    from gpodder.gtkui.desktop.shownotes import gPodderShownotes
    from gpodder.gtkui.desktop.episodeselector import gPodderEpisodeSelector
    from gpodder.gtkui.desktop.podcastdirectory import gPodderPodcastDirectory
    from gpodder.gtkui.desktop.dependencymanager import gPodderDependencyManager
    from gpodder.gtkui.interface.progress import ProgressIndicator
    try:
        from gpodder.gtkui.desktop.trayicon import GPodderStatusIcon
        have_trayicon = True
    except Exception, exc:
        log('Warning: Could not import gpodder.trayicon.', traceback=True)
        log('Warning: This probably means your PyGTK installation is too old!')
        have_trayicon = False
elif gpodder.ui.diablo:
    from gpodder.gtkui.download import DownloadStatusModel

    from gpodder.gtkui.maemo.channel import gPodderChannel
    from gpodder.gtkui.maemo.preferences import gPodderPreferences
    from gpodder.gtkui.maemo.shownotes import gPodderShownotes
    from gpodder.gtkui.maemo.episodeselector import gPodderEpisodeSelector
    from gpodder.gtkui.maemo.podcastdirectory import gPodderPodcastDirectory
    from gpodder.gtkui.maemo.mygpodder import MygPodderSettings
    from gpodder.gtkui.interface.progress import ProgressIndicator
    have_trayicon = False
elif gpodder.ui.fremantle:
    from gpodder.gtkui.frmntl.model import DownloadStatusModel
    from gpodder.gtkui.frmntl.model import EpisodeListModel
    from gpodder.gtkui.frmntl.model import PodcastListModel

    from gpodder.gtkui.maemo.channel import gPodderChannel
    from gpodder.gtkui.frmntl.preferences import gPodderPreferences
    from gpodder.gtkui.frmntl.shownotes import gPodderShownotes
    from gpodder.gtkui.frmntl.episodeselector import gPodderEpisodeSelector
    from gpodder.gtkui.frmntl.podcastdirectory import gPodderPodcastDirectory
    from gpodder.gtkui.frmntl.episodes import gPodderEpisodes
    from gpodder.gtkui.frmntl.downloads import gPodderDownloads
    from gpodder.gtkui.frmntl.progress import ProgressIndicator
    from gpodder.gtkui.frmntl.widgets import FancyProgressBar
    have_trayicon = False

    from gpodder.gtkui.frmntl.portrait import FremantleRotation
    from gpodder.gtkui.frmntl.mafw import MafwPlaybackMonitor
    from gpodder.gtkui.frmntl.hints import HINT_STRINGS
    from gpodder.gtkui.frmntl.network import NetworkManager

from gpodder.gtkui.interface.common import Orientation

from gpodder.gtkui.interface.welcome import gPodderWelcome

if gpodder.ui.maemo:
    import hildon

from gpodder.dbusproxy import DBusPodcastsProxy
from gpodder import hooks

class gPodder(BuilderWidget, dbus.service.Object):
    finger_friendly_widgets = ['btnCleanUpDownloads', 'button_search_episodes_clear', 'label2', 'labelDownloads', 'btnUpdateFeeds']

    ICON_GENERAL_ADD = 'general_add'
    ICON_GENERAL_REFRESH = 'general_refresh'

    # Delay until live search is started after typing stop
    LIVE_SEARCH_DELAY = 200

    def __init__(self, bus_name, config):
        dbus.service.Object.__init__(self, object_path=gpodder.dbus_gui_object_path, bus_name=bus_name)
        self.podcasts_proxy = DBusPodcastsProxy(lambda: self.channels, \
                self.on_itemUpdate_activate, \
                self.playback_episodes, \
                self.download_episode_list, \
                self.episode_object_by_uri, \
                bus_name)
        self.db = Database(gpodder.database_file)
        self.config = config
        BuilderWidget.__init__(self, None)
    
    def new(self):
        if gpodder.ui.diablo:
            import hildon
            self.app = hildon.Program()
            self.app.add_window(self.main_window)
            self.main_window.add_toolbar(self.toolbar)
            menu = gtk.Menu()
            for child in self.main_menu.get_children():
                child.reparent(menu)
            self.main_window.set_menu(self.set_finger_friendly(menu))
            self._last_orientation = Orientation.LANDSCAPE
        elif gpodder.ui.fremantle:
            import hildon
            self.app = hildon.Program()
            self.app.add_window(self.main_window)

            appmenu = hildon.AppMenu()

            for filter in (self.item_view_podcasts_all, \
                           self.item_view_podcasts_downloaded, \
                           self.item_view_podcasts_unplayed):
                button = gtk.ToggleButton()
                filter.connect_proxy(button)
                appmenu.add_filter(button)

            for action in (self.itemPreferences, \
                    self.item_downloads, \
                    self.itemRemoveOldEpisodes, \
                    self.item_unsubscribe, \
                    self.itemAbout):
                button = hildon.Button(gtk.HILDON_SIZE_AUTO,\
                        hildon.BUTTON_ARRANGEMENT_HORIZONTAL)
                action.connect_proxy(button)
                if action == self.item_downloads:
                    button.set_title(_('Downloads'))
                    button.set_value(_('Idle'))
                    self.button_downloads = button
                appmenu.append(button)

            def show_hint(button):
                self.show_message(random.choice(HINT_STRINGS), important=True)

            button = hildon.Button(gtk.HILDON_SIZE_AUTO,\
                    hildon.BUTTON_ARRANGEMENT_HORIZONTAL)
            button.set_title(_('Hint of the day'))
            button.connect('clicked', show_hint)
            appmenu.append(button)

            appmenu.show_all()
            self.main_window.set_app_menu(appmenu)

            # Initialize portrait mode / rotation manager
            self._fremantle_rotation = FremantleRotation('gPodder', \
                    self.main_window, \
                    gpodder.__version__, \
                    self.config.rotation_mode)

            # Initialize the Fremantle network manager
            self.network_manager = NetworkManager()

            if self.config.rotation_mode == FremantleRotation.ALWAYS:
                util.idle_add(self.on_window_orientation_changed, \
                        Orientation.PORTRAIT)
                self._last_orientation = Orientation.PORTRAIT
            else:
                self._last_orientation = Orientation.LANDSCAPE

            # Flag set when a notification is being shown (Maemo bug 11235)
            self._fremantle_notification_visible = False
        else:
            self._last_orientation = Orientation.LANDSCAPE
            self.toolbar.set_property('visible', self.config.show_toolbar)

        self.bluetooth_available = util.bluetooth_available()

        self.config.connect_gtk_window(self.gPodder, 'main_window')
        if not gpodder.ui.fremantle:
            self.config.connect_gtk_paned('paned_position', self.channelPaned)
        self.main_window.show()

        self.player_receiver = player.MediaPlayerDBusReceiver(self.on_played)

        if gpodder.ui.fremantle:
            # Create a D-Bus monitoring object that takes care of
            # tracking MAFW (Nokia Media Player) playback events
            # and sends episode playback status events via D-Bus
            self.mafw_monitor = MafwPlaybackMonitor(gpodder.dbus_session_bus)

        self.gPodder.connect('key-press-event', self.on_key_press)

        self.preferences_dialog = None
        self.config.add_observer(self.on_config_changed)

        self.tray_icon = None
        self.episode_shownotes_window = None
        self.new_episodes_window = None

        if gpodder.ui.desktop:
            # Mac OS X-specific UI tweaks: Native main menu integration
            # http://sourceforge.net/apps/trac/gtk-osx/wiki/Integrate
            if getattr(gtk.gdk, 'WINDOWING', 'x11') == 'quartz':
                try:
                    import igemacintegration as igemi

                    # Move the menu bar from the window to the Mac menu bar
                    self.mainMenu.hide()
                    igemi.ige_mac_menu_set_menu_bar(self.mainMenu)

                    # Reparent some items to the "Application" menu
                    for widget in ('/mainMenu/menuHelp/itemAbout', \
                                   '/mainMenu/menuPodcasts/itemPreferences'):
                        item = self.uimanager1.get_widget(widget)
                        group = igemi.ige_mac_menu_add_app_menu_group()
                        igemi.ige_mac_menu_add_app_menu_item(group, item, None)

                    quit_widget = '/mainMenu/menuPodcasts/itemQuit'
                    quit_item = self.uimanager1.get_widget(quit_widget)
                    igemi.ige_mac_menu_set_quit_menu_item(quit_item)
                except ImportError:
                    print >>sys.stderr, """
                    Warning: ige-mac-integration not found - no native menus.
                    """

            self.sync_ui = gPodderSyncUI(self.config, self.notification, \
                    self.main_window, self.show_confirmation, \
                    self.update_episode_list_icons, \
                    self.update_podcast_list_model, self.toolPreferences, \
                    gPodderEpisodeSelector, \
                    self.commit_changes_to_database)
        else:
            self.sync_ui = None

        self.download_status_model = DownloadStatusModel()
        self.download_queue_manager = download.DownloadQueueManager(self.config)

        if gpodder.ui.desktop:
            self.show_hide_tray_icon()
            self.itemShowAllEpisodes.set_active(self.config.podcast_list_view_all)
            self.itemShowNewEpisodes.set_active(self.config.podcast_list_view_new)
            self.itemShowToolbar.set_active(self.config.show_toolbar)
            self.itemShowDescription.set_active(self.config.episode_list_descriptions)

        if not gpodder.ui.fremantle:
            self.config.connect_gtk_spinbutton('max_downloads', self.spinMaxDownloads)
            self.config.connect_gtk_togglebutton('max_downloads_enabled', self.cbMaxDownloads)
            self.config.connect_gtk_spinbutton('limit_rate_value', self.spinLimitDownloads)
            self.config.connect_gtk_togglebutton('limit_rate', self.cbLimitDownloads)

            # When the amount of maximum downloads changes, notify the queue manager
            changed_cb = lambda spinbutton: self.download_queue_manager.spawn_threads()
            self.spinMaxDownloads.connect('value-changed', changed_cb)

        self.default_title = 'gPodder'
        if gpodder.__version__.rfind('git') != -1:
            self.set_title('gPodder %s' % gpodder.__version__)
        else:
            title = self.gPodder.get_title()
            if title is not None:
                self.set_title(title)
            else:
                self.set_title(_('gPodder'))

        self.cover_downloader = CoverDownloader()

        # Generate list models for podcasts and their episodes
        self.podcast_list_model = PodcastListModel(self.cover_downloader)

        self.cover_downloader.register('cover-available', self.cover_download_finished)
        self.cover_downloader.register('cover-removed', self.cover_file_removed)

        if gpodder.ui.fremantle:
            # Work around Maemo bug #4718
            self.button_refresh.set_name('HildonButton-finger')
            self.button_subscribe.set_name('HildonButton-finger')

            self.button_refresh.set_sensitive(False)
            self.button_subscribe.set_sensitive(False)

            self.button_subscribe.set_image(gtk.image_new_from_icon_name(\
                    self.ICON_GENERAL_ADD, gtk.ICON_SIZE_BUTTON))
            self.button_refresh.set_image(gtk.image_new_from_icon_name(\
                    self.ICON_GENERAL_REFRESH, gtk.ICON_SIZE_BUTTON))

            # Make the button scroll together with the TreeView contents
            action_area_box = self.treeChannels.get_action_area_box()
            for child in self.buttonbox:
                child.reparent(action_area_box)
            self.vbox.remove(self.buttonbox)
            self.treeChannels.set_action_area_visible(True)

            # Set up a very nice progress bar setup
            self.fancy_progress_bar = FancyProgressBar(self.main_window, \
                    self.on_btnCancelFeedUpdate_clicked)
            self.pbFeedUpdate = self.fancy_progress_bar.progress_bar
            self.pbFeedUpdate.set_ellipsize(pango.ELLIPSIZE_MIDDLE)
            self.vbox.pack_start(self.fancy_progress_bar.event_box, False)

            from gpodder.gtkui.frmntl import style
            sub_font = style.get_font_desc('SmallSystemFont')
            sub_color = style.get_color('SecondaryTextColor')
            sub = (sub_font.to_string(), sub_color.to_string())
            sub = '<span font_desc="%s" foreground="%s">%%s</span>' % sub
            self.label_footer.set_markup(sub % gpodder.__copyright__)

            hildon.hildon_gtk_window_set_progress_indicator(self.main_window, True)
            while gtk.events_pending():
                gtk.main_iteration(False)

            try:
                # Try to get the real package version from dpkg
                p = subprocess.Popen(['dpkg-query', '-W', '-f=${Version}', 'gpodder'], stdout=subprocess.PIPE)
                version, _stderr = p.communicate()
                del _stderr
                del p
            except:
                version = gpodder.__version__
            self.label_footer.set_markup(sub % ('v %s' % version))
            self.label_footer.hide()

            self.episodes_window = gPodderEpisodes(self.main_window, \
                    on_treeview_expose_event=self.on_treeview_expose_event, \
                    show_episode_shownotes=self.show_episode_shownotes, \
                    update_podcast_list_model=self.update_podcast_list_model, \
                    on_itemRemoveChannel_activate=self.on_itemRemoveChannel_activate, \
                    item_view_episodes_all=self.item_view_episodes_all, \
                    item_view_episodes_unplayed=self.item_view_episodes_unplayed, \
                    item_view_episodes_downloaded=self.item_view_episodes_downloaded, \
                    item_view_episodes_undeleted=self.item_view_episodes_undeleted, \
                    on_entry_search_episodes_changed=self.on_entry_search_episodes_changed, \
                    on_entry_search_episodes_key_press=self.on_entry_search_episodes_key_press, \
                    hide_episode_search=self.hide_episode_search, \
                    on_itemUpdateChannel_activate=self.on_itemUpdateChannel_activate, \
                    playback_episodes=self.playback_episodes, \
                    delete_episode_list=self.delete_episode_list, \
                    episode_list_status_changed=self.episode_list_status_changed, \
                    download_episode_list=self.download_episode_list, \
                    episode_is_downloading=self.episode_is_downloading, \
                    show_episode_in_download_manager=self.show_episode_in_download_manager, \
                    add_download_task_monitor=self.add_download_task_monitor, \
                    remove_download_task_monitor=self.remove_download_task_monitor, \
                    for_each_episode_set_task_status=self.for_each_episode_set_task_status, \
                    on_itemUpdate_activate=self.on_itemUpdate_activate, \
                    show_delete_episodes_window=self.show_delete_episodes_window, \
                    cover_downloader=self.cover_downloader)

            # Expose objects for episode list type-ahead find
            self.hbox_search_episodes = self.episodes_window.hbox_search_episodes
            self.entry_search_episodes = self.episodes_window.entry_search_episodes
            self.button_search_episodes_clear = self.episodes_window.button_search_episodes_clear

            self.downloads_window = gPodderDownloads(self.main_window, \
                    on_treeview_expose_event=self.on_treeview_expose_event, \
                    cleanup_downloads=self.cleanup_downloads, \
                    _for_each_task_set_status=self._for_each_task_set_status, \
                    downloads_list_get_selection=self.downloads_list_get_selection, \
                    _config=self.config)

            self.treeAvailable = self.episodes_window.treeview
            self.treeDownloads = self.downloads_window.treeview

        # Source IDs for timeouts for search-as-you-type
        self._podcast_list_search_timeout = None
        self._episode_list_search_timeout = None

        # Init the treeviews that we use
        self.init_podcast_list_treeview()
        self.init_episode_list_treeview()
        self.init_download_list_treeview()

        if self.config.podcast_list_hide_boring:
            self.item_view_hide_boring_podcasts.set_active(True)

        self.currently_updating = False

        if gpodder.ui.maemo or self.config.enable_fingerscroll:
            self.context_menu_mouse_button = 1
        else:
            self.context_menu_mouse_button = 3

        if self.config.start_iconified:
            self.iconify_main_window()

        self.download_tasks_seen = set()
        self.download_list_update_enabled = False
        self.download_task_monitors = set()

        # Subscribed channels
        self.active_channel = None
        self.channels = PodcastChannel.load_from_db(self.db, self.config.download_dir)
        self.channel_list_changed = True
        self.update_podcasts_tab()

        # load list of user applications for audio playback
        self.user_apps_reader = UserAppsReader(['audio', 'video'])
        threading.Thread(target=self.user_apps_reader.read).start()

        # Set the "Device" menu item for the first time
        if gpodder.ui.desktop:
            self.update_item_device()

        # Set up the first instance of MygPoClient
        self.mygpo_client = my.MygPoClient(self.config)

        # Now, update the feed cache, when everything's in place
        if not gpodder.ui.fremantle:
            self.btnUpdateFeeds.show()
        self.updating_feed_cache = False
        self.feed_cache_update_cancelled = False

        # Always load the podcast list, even when updating later (bug 1337)
        self.update_feed_cache(force_update=False)

        if self.config.update_on_startup:
            self.update_feed_cache(force_update=True)

        self.message_area = None

        def find_partial_downloads():
            # Look for partial file downloads
            partial_files = glob.glob(os.path.join(self.config.download_dir, '*', '*.partial'))
            count = len(partial_files)
            resumable_episodes = []
            if count:
                if not gpodder.ui.fremantle:
                    util.idle_add(self.wNotebook.set_current_page, 1)
                indicator = ProgressIndicator(_('Loading incomplete downloads'), \
                        _('Some episodes have not finished downloading in a previous session.'), \
                        False, self.get_dialog_parent())
                indicator.on_message(N_('%(count)d partial file', '%(count)d partial files', count) % {'count':count})

                candidates = [f[:-len('.partial')] for f in partial_files]
                found = 0

                for c in self.channels:
                    for e in c.get_all_episodes():
                        filename = e.local_filename(create=False, check_only=True)
                        if filename in candidates:
                            log('Found episode: %s', e.title, sender=self)
                            found += 1
                            indicator.on_message(e.title)
                            indicator.on_progress(float(found)/count)
                            candidates.remove(filename)
                            partial_files.remove(filename+'.partial')

                            if os.path.exists(filename):
                                # The file has already been downloaded;
                                # remove the leftover partial file
                                util.delete_file(filename+'.partial')
                            else:
                                resumable_episodes.append(e)

                        if not candidates:
                            break

                    if not candidates:
                        break

                for f in partial_files:
                    log('Partial file without episode: %s', f, sender=self)
                    util.delete_file(f)

                util.idle_add(indicator.on_finished)

                if len(resumable_episodes):
                    def offer_resuming():
                        self.download_episode_list_paused(resumable_episodes)
                        if not gpodder.ui.fremantle:
                            resume_all = gtk.Button(_('Resume all'))
                            #resume_all.set_border_width(0)
                            def on_resume_all(button):
                                selection = self.treeDownloads.get_selection()
                                selection.select_all()
                                selected_tasks, can_queue, can_cancel, can_pause, can_remove, can_force = self.downloads_list_get_selection()
                                selection.unselect_all()
                                self._for_each_task_set_status(selected_tasks, download.DownloadTask.QUEUED)
                                self.message_area.hide()
                            resume_all.connect('clicked', on_resume_all)

                            self.message_area = SimpleMessageArea(_('Incomplete downloads from a previous session were found.'), (resume_all,))
                            self.vboxDownloadStatusWidgets.pack_start(self.message_area, expand=False)
                            self.vboxDownloadStatusWidgets.reorder_child(self.message_area, 0)
                            self.message_area.show_all()
                        self.clean_up_downloads(delete_partial=False)
                    util.idle_add(offer_resuming)
                elif not gpodder.ui.fremantle:
                    util.idle_add(self.wNotebook.set_current_page, 0)
            else:
                util.idle_add(self.clean_up_downloads, True)
        threading.Thread(target=find_partial_downloads).start()

        # Start the auto-update procedure
        self._auto_update_timer_source_id = None
        if self.config.auto_update_feeds:
            self.restart_auto_update_timer()

        # Delete old episodes if the user wishes to
        if self.config.auto_remove_played_episodes and \
                self.config.episode_old_age > 0:
            old_episodes = list(self.get_expired_episodes())
            if len(old_episodes) > 0:
                self.delete_episode_list(old_episodes, confirm=False)
                self.update_podcast_list_model(set(e.channel.url for e in old_episodes))

        if gpodder.ui.fremantle:
            hildon.hildon_gtk_window_set_progress_indicator(self.main_window, False)
            self.button_refresh.set_sensitive(True)
            self.button_subscribe.set_sensitive(True)
            self.main_window.set_title(_('gPodder'))
            hildon.hildon_gtk_window_take_screenshot(self.main_window, True)

        # Do the initial sync with the web service
        util.idle_add(self.mygpo_client.flush, True)

        # First-time users should be asked if they want to see the OPML
        if not self.channels and not gpodder.ui.fremantle:
            util.idle_add(self.on_itemUpdate_activate)
        
        # initialise the html notes
        self._read_timer_source_id = None
        
        # initialise text view
        self.textview.modify_bg(gtk.STATE_NORMAL, gtk.gdk.color_parse('#ffffff'))
        self.hovering_over_link = False
        self.hand_cursor = gtk.gdk.Cursor(gtk.gdk.HAND2)
        self.regular_cursor = gtk.gdk.Cursor(gtk.gdk.XTERM)

        if self.config.enable_html_shownotes:
            try:
                import webkit
                webview_signals = gobject.signal_list_names(webkit.WebView)
                if 'navigation-policy-decision-requested' in webview_signals:
                    setattr(self, 'have_webkit', True)
                    setattr(self, 'htmlview', webkit.WebView())
                else:
                    log('Your WebKit is too old (see bug 1001).', sender=self)
                    setattr(self, 'have_webkit', False)

                def navigation_policy_decision(wv, fr, req, action, decision):
                    REASON_LINK_CLICKED, REASON_OTHER = 0, 5
                    if action.get_reason() == REASON_LINK_CLICKED:
                        util.open_website(req.get_uri())
                        decision.ignore()
                    elif action.get_reason() == REASON_OTHER:
                        decision.use()
                    else:
                        decision.ignore()

                self.htmlview.connect('navigation-policy-decision-requested', \
                        navigation_policy_decision)

                self.scrolled_window.remove(self.scrolled_window.get_child())
                self.scrolled_window.add(self.htmlview)
                self.textview = None
                self.htmlview.load_html_string('', '')
                self.htmlview.show()
            except ImportError:
                setattr(self, 'have_webkit', False)
        else:
            setattr(self, 'have_webkit', False)


   # Links can be activated by pressing Enter.
    def on_textview_key_press_event(self, text_view, event):
        if (event.keyval == gtk.keysyms.Return or
            event.keyval == gtk.keysyms.KP_Enter):
            buffer = text_view.get_buffer()
            iter = buffer.get_iter_at_mark(buffer.get_insert())
            self.textview_follow_if_link(iter)
        return False

    # Links can also be activated by clicking.
    def on_textview_event_after(self, text_view, event):
        if event.type != gtk.gdk.BUTTON_RELEASE:
            return False
        if event.button != 1:
            return False
        buffer = text_view.get_buffer()

        # we shouldn't follow a link if the user has selected something
        try:
            start, end = buffer.get_selection_bounds()
        except ValueError:
            # If there is nothing selected, None is return
            pass
        else:
            if start.get_offset() != end.get_offset():
                return False

        x, y = text_view.window_to_buffer_coords(gtk.TEXT_WINDOW_WIDGET,
            int(event.x), int(event.y))
        iter = text_view.get_iter_at_location(x, y)

        self.textview_follow_if_link(iter)
        return False


    # Looks at all tags covering the position (x, y) in the text view,
    # and if one of them is a link, change the cursor to the "hands" cursor
    # typically used by web browsers.
    def textview_set_cursor_if_appropriate(self, text_view, x, y):
        hovering = False

        buffer = text_view.get_buffer()
        iter = text_view.get_iter_at_location(x, y)

        tags = iter.get_tags()
        for tag in tags:
            page = tag.get_data("page")
            if page is not None:
                hovering = True
                break
                
        if hovering != self.hovering_over_link:
            self.hovering_over_link = hovering

        if self.hovering_over_link:
            text_view.get_window(gtk.TEXT_WINDOW_TEXT).set_cursor(self.hand_cursor)
        else:
            text_view.get_window(gtk.TEXT_WINDOW_TEXT).set_cursor(self.regular_cursor)

    # Update the cursor image if the pointer moved.
    def on_textview_motion_notify_event(self, text_view, event):
        x, y = text_view.window_to_buffer_coords(gtk.TEXT_WINDOW_WIDGET,
            int(event.x), int(event.y))
        self.textview_set_cursor_if_appropriate(text_view, x, y)
        text_view.window.get_pointer()
        return False

    # Also update the cursor image if the window becomes visible
    # (e.g. when a window covering it got iconified).
    def on_textview_visibility_notify_event(self, text_view, event):
        wx, wy, mod = text_view.window.get_pointer()
        bx, by = text_view.window_to_buffer_coords(gtk.TEXT_WINDOW_WIDGET, wx, wy)

        self.textview_set_cursor_if_appropriate(text_view, bx, by)
        return False

    def textview_insert_link(self, buffer, iter, text, page):
        ''' Inserts a piece of text into the buffer, giving it the usual
            appearance of a hyperlink in a web browser: blue and underlined.
            Additionally, attaches some data on the tag, to make it recognizable
            as a link.
        '''
        tag = buffer.create_tag(None,
            foreground="blue", underline=pango.UNDERLINE_SINGLE)
        tag.set_data("page", page)
        buffer.insert_with_tags(iter, text, tag)

    def textview_follow_if_link(self, iter):
        ''' Looks at all tags covering the position of iter in the text view,
            and if one of them is a link, follow it by showing the page identified
            by the data attached to it.
        '''
        tags = iter.get_tags()
        for tag in tags:
            page = tag.get_data("page")
            if page is not None:
                util.open_website(page)
                break

    def display_embedded_notes(self,episode):
        self.pre_display_notes()
        # breaks selection...
        #while gtk.events_pending():
        #  gtk.main_iteration(False)
        
        # Load the shownotes into the UI
        self.clear_embedded_notes()
        self.load_embedded_notes(episode)
	
    def pre_display_notes(self):
        if self.have_webkit:
            self.htmlview.load_html_string('<html><head></head><body><em>%s</em></body></html>' % _('Loading shownotes...'), '')
        else:
            self.b = gtk.TextBuffer()
            self.textview.set_buffer(self.b)

    def clear_embedded_notes(self):
        if self.have_webkit:
            self.htmlview.load_html_string('', '')
        else:
            self.textview.get_buffer().set_text('')
        self.restart_read_timer(None)


    def load_embedded_notes(self,episode):
        if episode is None:
            return
        # Now do the stuff that takes a bit longer...
        heading = episode.title
        subheading = _('from %s') % (episode.channel.title)
        description = episode.description

        if self.have_webkit:
            global SHOWNOTES_HTML_TEMPLATE

            # Get the description - if it looks like plaintext, replace the
            # newline characters with line breaks for the HTML view
            description = episode.description
            if '<' not in description:
                description = description.replace('\n', '<br>')

            args = (
                    episode.link,
                    saxutils.escape(heading),
                    saxutils.escape(subheading),
                    description
            )
            url = os.path.dirname(episode.channel.url)
            self.htmlview.load_html_string(SHOWNOTES_HTML_TEMPLATE % args, url)
        else:
            tag = self.b.create_tag('heading', scale=pango.SCALE_LARGE, \
                  weight=pango.WEIGHT_BOLD, underline=pango.UNDERLINE_SINGLE)
            tag.set_data('page',episode.link)
            
            self.b.create_tag('subheading', scale=pango.SCALE_SMALL)

            self.b.insert_with_tags_by_name(self.b.get_end_iter(), heading, 'heading')
            self.b.insert_at_cursor('\n')
            self.b.insert_with_tags_by_name(self.b.get_end_iter(), subheading, 'subheading')
            self.b.insert_at_cursor('\n\n')
            self.b.insert(self.b.get_end_iter(), util.remove_html_tags(description))
            self.b.place_cursor(self.b.get_start_iter())
        self.restart_read_timer(episode)
        
    def on_visit_website_button_clicked(self, widget=None):
        if self.episode and self.episode.link:
            util.open_website(self.episode.link)

    def episode_object_by_uri(self, uri):
        """Get an episode object given a local or remote URI

        This can be used to quickly access an episode object
        when all we have is its download filename or episode
        URL (e.g. from external D-Bus calls / signals, etc..)
        """
        if uri.startswith('/'):
            uri = 'file://' + urllib.quote(uri)

        prefix = 'file://' + urllib.quote(self.config.download_dir)

        if uri.startswith(prefix):
            # File is on the local filesystem in the download folder
            filename = urllib.unquote(uri[len(prefix):])
            file_parts = [x for x in filename.split(os.sep) if x]

            if len(file_parts) == 2:
                dir_name, filename = file_parts
                channels = [c for c in self.channels if c.foldername == dir_name]
                if len(channels) == 1:
                    channel = channels[0]
                    return channel.get_episode_by_filename(filename)
        else:
            # Possibly remote file - search the database for a podcast
            channel_id = self.db.get_channel_id_from_episode_url(uri)

            if channel_id is not None:
                channels = [c for c in self.channels if c.id == channel_id]
                if len(channels) == 1:
                    channel = channels[0]
                    return channel.get_episode_by_url(uri)

        return None

    def on_played(self, start, end, total, file_uri):
        """Handle the "played" signal from a media player"""
        if start == 0 and end == 0 and total == 0:
            # Ignore bogus play event
            return
        elif end < start + 5:
            # Ignore "less than five seconds" segments,
            # as they can happen with seeking, etc...
            return

        log('Received play action: %s (%d, %d, %d)', file_uri, start, end, total, sender=self)
        episode = self.episode_object_by_uri(file_uri)

        if episode is not None:
            file_type = episode.file_type()

            now = time.time()
            if total > 0:
                episode.total_time = total
            elif total == 0:
                # Assume the episode's total time for the action
                total = episode.total_time
            assert episode.current_position_updated is None or \
                    now >= episode.current_position_updated
            episode.current_position = end
            episode.current_position_updated = now
            episode.mark(is_played=True)
            episode.save()
            self.db.commit()
            self.update_episode_list_icons([episode.url])
            self.update_podcast_list_model([episode.channel.url])

            # Submit this action to the webservice
            self.mygpo_client.on_playback_full(episode, \
                    start, end, total)

    def on_add_remove_podcasts_mygpo(self):
        actions = self.mygpo_client.get_received_actions()
        if not actions:
            return False

        existing_urls = [c.url for c in self.channels]

        # Columns for the episode selector window - just one...
        columns = (
            ('description', None, None, _('Action')),
        )

        # A list of actions that have to be chosen from
        changes = []

        # Actions that are ignored (already carried out)
        ignored = []

        for action in actions:
            if action.is_add and action.url not in existing_urls:
                changes.append(my.Change(action))
            elif action.is_remove and action.url in existing_urls:
                podcast_object = None
                for podcast in self.channels:
                    if podcast.url == action.url:
                        podcast_object = podcast
                        break
                changes.append(my.Change(action, podcast_object))
            else:
                log('Ignoring action: %s', action, sender=self)
                ignored.append(action)

        # Confirm all ignored changes
        self.mygpo_client.confirm_received_actions(ignored)

        def execute_podcast_actions(selected):
            add_list = [c.action.url for c in selected if c.action.is_add]
            remove_list = [c.podcast for c in selected if c.action.is_remove]

            # Apply the accepted changes locally
            self.add_podcast_list(add_list)
            self.remove_podcast_list(remove_list, confirm=False)

            # All selected items are now confirmed
            self.mygpo_client.confirm_received_actions(c.action for c in selected)

            # Revert the changes on the server
            rejected = [c.action for c in changes if c not in selected]
            self.mygpo_client.reject_received_actions(rejected)

        def ask():
            # We're abusing the Episode Selector again ;) -- thp
            gPodderEpisodeSelector(self.main_window, \
                    title=_('Confirm changes from gpodder.net'), \
                    instructions=_('Select the actions you want to carry out.'), \
                    episodes=changes, \
                    columns=columns, \
                    size_attribute=None, \
                    stock_ok_button=gtk.STOCK_APPLY, \
                    callback=execute_podcast_actions, \
                    _config=self.config)

        # There are some actions that need the user's attention
        if changes:
            util.idle_add(ask)
            return True

        # We have no remaining actions - no selection happens
        return False

    def rewrite_urls_mygpo(self):
        # Check if we have to rewrite URLs since the last add
        rewritten_urls = self.mygpo_client.get_rewritten_urls()

        for rewritten_url in rewritten_urls:
            if not rewritten_url.new_url:
                continue

            for channel in self.channels:
                if channel.url == rewritten_url.old_url:
                    log('Updating URL of %s to %s', channel, \
                            rewritten_url.new_url, sender=self)
                    channel.url = rewritten_url.new_url
                    channel.save()
                    self.channel_list_changed = True
                    util.idle_add(self.update_episode_list_model)
                    break

    def on_send_full_subscriptions(self):
        # Send the full subscription list to the gpodder.net client
        # (this will overwrite the subscription list on the server)
        indicator = ProgressIndicator(_('Uploading subscriptions'), \
                _('Your subscriptions are being uploaded to the server.'), \
                False, self.get_dialog_parent())

        try:
            self.mygpo_client.set_subscriptions([c.url for c in self.channels])
            util.idle_add(self.show_message, _('List uploaded successfully.'))
        except Exception, e:
            def show_error(e):
                message = str(e)
                if not message:
                    message = e.__class__.__name__
                self.show_message(message, \
                        _('Error while uploading'), \
                        important=True)
            util.idle_add(show_error, e)

        util.idle_add(indicator.on_finished)

    def on_podcast_selected(self, treeview, path, column):
        # for Maemo 5's UI
        model = treeview.get_model()
        channel = model.get_value(model.get_iter(path), \
                PodcastListModel.C_CHANNEL)
        self.active_channel = channel
        self.update_episode_list_model()
        self.episodes_window.channel = self.active_channel
        self.episodes_window.show()

    def on_button_subscribe_clicked(self, button):
        self.on_itemImportChannels_activate(button)

    def on_button_downloads_clicked(self, widget):
        self.downloads_window.show()

    def show_episode_in_download_manager(self, episode):
        self.downloads_window.show()
        model = self.treeDownloads.get_model()
        selection = self.treeDownloads.get_selection()
        selection.unselect_all()
        it = model.get_iter_first()
        while it is not None:
            task = model.get_value(it, DownloadStatusModel.C_TASK)
            if task.episode.url == episode.url:
                selection.select_iter(it)
                # FIXME: Scroll to selection in pannable area
                break
            it = model.iter_next(it)

    def for_each_episode_set_task_status(self, episodes, status):
        episode_urls = set(episode.url for episode in episodes)
        model = self.treeDownloads.get_model()
        selected_tasks = [(gtk.TreeRowReference(model, row.path), \
                           model.get_value(row.iter, \
                           DownloadStatusModel.C_TASK)) for row in model \
                           if model.get_value(row.iter, DownloadStatusModel.C_TASK).url \
                           in episode_urls]
        self._for_each_task_set_status(selected_tasks, status)

    def on_window_orientation_changed(self, orientation):
        self._last_orientation = orientation
        if self.preferences_dialog is not None:
            self.preferences_dialog.on_window_orientation_changed(orientation)

        treeview = self.treeChannels
        if orientation == Orientation.PORTRAIT:
            treeview.set_action_area_orientation(gtk.ORIENTATION_VERTICAL)
            # Work around Maemo bug #4718
            self.button_subscribe.set_name('HildonButton-thumb')
            self.button_refresh.set_name('HildonButton-thumb')
        else:
            treeview.set_action_area_orientation(gtk.ORIENTATION_HORIZONTAL)
            # Work around Maemo bug #4718
            self.button_subscribe.set_name('HildonButton-finger')
            self.button_refresh.set_name('HildonButton-finger')

        if gpodder.ui.fremantle:
            self.fancy_progress_bar.relayout()

    def on_treeview_podcasts_selection_changed(self, selection):
        model, iter = selection.get_selected()
        if iter is None:
            self.active_channel = None
            self.episode_list_model.clear()

    def on_treeview_button_pressed(self, treeview, event):
        if event.window != treeview.get_bin_window():
            return False

        TreeViewHelper.save_button_press_event(treeview, event)

        if getattr(treeview, TreeViewHelper.ROLE) == \
                TreeViewHelper.ROLE_PODCASTS:
            return self.currently_updating

        return event.button == self.context_menu_mouse_button and \
                gpodder.ui.desktop

    def on_treeview_podcasts_button_released(self, treeview, event):
        if event.window != treeview.get_bin_window():
            return False

        if gpodder.ui.maemo:
            return self.treeview_channels_handle_gestures(treeview, event)
        return self.treeview_channels_show_context_menu(treeview, event)

    def on_treeview_episodes_button_released(self, treeview, event):
        if event.window != treeview.get_bin_window():
            return False

        if self.config.enable_fingerscroll or self.config.maemo_enable_gestures:
            return self.treeview_available_handle_gestures(treeview, event)

        return self.treeview_available_show_context_menu(treeview, event)

    def on_treeview_downloads_button_released(self, treeview, event):
        if event.window != treeview.get_bin_window():
            return False

        return self.treeview_downloads_show_context_menu(treeview, event)

    def on_entry_search_podcasts_changed(self, editable):
        if self.hbox_search_podcasts.get_property('visible'):
            def set_search_term(self, text):
                self.podcast_list_model.set_search_term(text)
                self._podcast_list_search_timeout = None
                return False

            if self._podcast_list_search_timeout is not None:
                gobject.source_remove(self._podcast_list_search_timeout)
            self._podcast_list_search_timeout = gobject.timeout_add(\
                    self.LIVE_SEARCH_DELAY, \
                    set_search_term, self, editable.get_chars(0, -1))

    def on_entry_search_podcasts_key_press(self, editable, event):
        if event.keyval == gtk.keysyms.Escape:
            self.hide_podcast_search()
            return True

    def hide_podcast_search(self, *args):
        if self._podcast_list_search_timeout is not None:
            gobject.source_remove(self._podcast_list_search_timeout)
            self._podcast_list_search_timeout = None
        self.hbox_search_podcasts.hide()
        self.entry_search_podcasts.set_text('')
        self.podcast_list_model.set_search_term(None)
        self.treeChannels.grab_focus()

    def show_podcast_search(self, input_char):
        self.hbox_search_podcasts.show()
        self.entry_search_podcasts.insert_text(input_char, -1)
        self.entry_search_podcasts.grab_focus()
        self.entry_search_podcasts.set_position(-1)

    def init_podcast_list_treeview(self):
        # Set up podcast channel tree view widget
        if gpodder.ui.fremantle:
            if self.config.podcast_list_view_mode == EpisodeListModel.VIEW_DOWNLOADED:
                self.item_view_podcasts_downloaded.set_active(True)
            elif self.config.podcast_list_view_mode == EpisodeListModel.VIEW_UNPLAYED:
                self.item_view_podcasts_unplayed.set_active(True)
            else:
                self.item_view_podcasts_all.set_active(True)
            self.podcast_list_model.set_view_mode(self.config.podcast_list_view_mode)

        iconcolumn = gtk.TreeViewColumn('')
        iconcell = gtk.CellRendererPixbuf()
        iconcolumn.pack_start(iconcell, False)
        iconcolumn.add_attribute(iconcell, 'pixbuf', PodcastListModel.C_COVER)
        self.treeChannels.append_column(iconcolumn)

        namecolumn = gtk.TreeViewColumn('')
        namecell = gtk.CellRendererText()
        namecell.set_property('ellipsize', pango.ELLIPSIZE_END)
        namecolumn.pack_start(namecell, True)
        namecolumn.add_attribute(namecell, 'markup', PodcastListModel.C_DESCRIPTION)

        if gpodder.ui.fremantle:
            countcell = gtk.CellRendererText()
            from gpodder.gtkui.frmntl import style
            countcell.set_property('font-desc', style.get_font_desc('EmpSystemFont'))
            countcell.set_property('foreground-gdk', style.get_color('SecondaryTextColor'))
            countcell.set_property('alignment', pango.ALIGN_RIGHT)
            countcell.set_property('xalign', 1.)
            countcell.set_property('xpad', 5)
            namecolumn.pack_start(countcell, False)
            namecolumn.add_attribute(countcell, 'text', PodcastListModel.C_DOWNLOADS)
            namecolumn.add_attribute(countcell, 'visible', PodcastListModel.C_DOWNLOADS)
        else:
            iconcell = gtk.CellRendererPixbuf()
            iconcell.set_property('xalign', 1.0)
            namecolumn.pack_start(iconcell, False)
            namecolumn.add_attribute(iconcell, 'pixbuf', PodcastListModel.C_PILL)
            namecolumn.add_attribute(iconcell, 'visible', PodcastListModel.C_PILL_VISIBLE)

        self.treeChannels.append_column(namecolumn)

        self.treeChannels.set_model(self.podcast_list_model.get_filtered_model())

        iter = self.treeChannels.get_model().get_iter_root()
        if iter != None:
            iter = self.treeChannels.get_model().iter_next(iter)
            if iter != None:
                self.treeChannels.get_selection().select_iter(iter)
            else:
                print "None 2nd line"
        else:
            print "None 1st line"

        # When no podcast is selected, clear the episode list model
        selection = self.treeChannels.get_selection()
        selection.connect('changed', self.on_treeview_podcasts_selection_changed)

        # Set up type-ahead find for the podcast list
        def on_key_press(treeview, event):
            if event.keyval == gtk.keysyms.Escape:
                self.hide_podcast_search()
            elif gpodder.ui.fremantle and event.keyval == gtk.keysyms.BackSpace:
                self.hide_podcast_search()
            elif event.state & gtk.gdk.CONTROL_MASK:
                # Don't handle type-ahead when control is pressed (so shortcuts
                # with the Ctrl key still work, e.g. Ctrl+A, ...)
                return True
            else:
                unicode_char_id = gtk.gdk.keyval_to_unicode(event.keyval)
                if unicode_char_id == 0:
                    return False
                input_char = unichr(unicode_char_id)
                self.show_podcast_search(input_char)
            return True
        self.treeChannels.connect('key-press-event', on_key_press)

        # Enable separators to the podcast list to separate special podcasts
        # from others (this is used for the "all episodes" view)
        self.treeChannels.set_row_separator_func(PodcastListModel.row_separator_func)
        TreeViewHelper.set(self.treeChannels, TreeViewHelper.ROLE_PODCASTS)


    def on_entry_search_episodes_changed(self, editable):
        if self.hbox_search_episodes.get_property('visible'):
            def set_search_term(self, text):
                self.episode_list_model.set_search_term(text)
                self._episode_list_search_timeout = None
                return False

            if self._episode_list_search_timeout is not None:
                gobject.source_remove(self._episode_list_search_timeout)
            self._episode_list_search_timeout = gobject.timeout_add(\
                    self.LIVE_SEARCH_DELAY, \
                    set_search_term, self, editable.get_chars(0, -1))

    def on_entry_search_episodes_key_press(self, editable, event):
        if event.keyval == gtk.keysyms.Escape:
            self.hide_episode_search()
            return True

    def hide_episode_search(self, *args):
        if self._episode_list_search_timeout is not None:
            gobject.source_remove(self._episode_list_search_timeout)
            self._episode_list_search_timeout = None
        self.hbox_search_episodes.hide()
        self.entry_search_episodes.set_text('')
        self.episode_list_model.set_search_term(None)
        self.treeAvailable.grab_focus()

    def show_episode_search(self, input_char):
        self.hbox_search_episodes.show()
        self.entry_search_episodes.insert_text(input_char, -1)
        self.entry_search_episodes.grab_focus()
        self.entry_search_episodes.set_position(-1)

    def init_episode_list_treeview(self):
        # For loading the list model
        self.episode_list_model = EpisodeListModel(self.on_episode_list_filter_changed)

        if self.config.episode_list_view_mode == EpisodeListModel.VIEW_UNDELETED:
            self.item_view_episodes_undeleted.set_active(True)
        elif self.config.episode_list_view_mode == EpisodeListModel.VIEW_DOWNLOADED:
            self.item_view_episodes_downloaded.set_active(True)
        elif self.config.episode_list_view_mode == EpisodeListModel.VIEW_UNPLAYED:
            self.item_view_episodes_unplayed.set_active(True)
        else:
            self.item_view_episodes_all.set_active(True)

        self.episode_list_model.set_view_mode(self.config.episode_list_view_mode)

        self.treeAvailable.set_model(self.episode_list_model.get_filtered_model())

        TreeViewHelper.set(self.treeAvailable, TreeViewHelper.ROLE_EPISODES)

        iconcell = gtk.CellRendererPixbuf()
        iconcell.set_property('stock-size', gtk.ICON_SIZE_BUTTON)
        if gpodder.ui.maemo:
            iconcell.set_fixed_size(50, 50)
        else:
            iconcell.set_fixed_size(40, -1)

        namecell = gtk.CellRendererText()
        namecell.set_property('ellipsize', pango.ELLIPSIZE_END)
        namecolumn = gtk.TreeViewColumn(_('Episode'))
        namecolumn.pack_start(iconcell, False)
        namecolumn.add_attribute(iconcell, 'icon-name', EpisodeListModel.C_STATUS_ICON)
        namecolumn.pack_start(namecell, True)
        namecolumn.add_attribute(namecell, 'markup', EpisodeListModel.C_DESCRIPTION)
        if gpodder.ui.fremantle:
            namecolumn.set_sizing(gtk.TREE_VIEW_COLUMN_FIXED)
        else:
            namecolumn.set_sort_column_id(EpisodeListModel.C_DESCRIPTION)
            namecolumn.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
            namecolumn.set_resizable(True)
        namecolumn.set_expand(True)

        if gpodder.ui.fremantle:
            from gpodder.gtkui.frmntl import style
            timecell = gtk.CellRendererText()
            timecell.set_property('font-desc', style.get_font_desc('SmallSystemFont'))
            timecell.set_property('foreground-gdk', style.get_color('SecondaryTextColor'))
            timecell.set_property('alignment', pango.ALIGN_RIGHT)
            timecell.set_property('xalign', 1.)
            timecell.set_property('xpad', 5)
            timecell.set_property('yalign', .85)
            namecolumn.pack_start(timecell, False)
            namecolumn.add_attribute(timecell, 'text', EpisodeListModel.C_TIME)
            namecolumn.add_attribute(timecell, 'visible', EpisodeListModel.C_TIME_VISIBLE)
        else:
            lockcell = gtk.CellRendererPixbuf()
            lockcell.set_fixed_size(40, -1)
            lockcell.set_property('stock-size', gtk.ICON_SIZE_MENU)
            lockcell.set_property('icon-name', 'emblem-readonly')
            namecolumn.pack_start(lockcell, False)
            namecolumn.add_attribute(lockcell, 'visible', EpisodeListModel.C_LOCKED)

        sizecell = gtk.CellRendererText()
        sizecell.set_property('xalign', 1)
        sizecolumn = gtk.TreeViewColumn(_('Size'), sizecell, text=EpisodeListModel.C_FILESIZE_TEXT)
        sizecolumn.set_sort_column_id(EpisodeListModel.C_FILESIZE)

        releasecell = gtk.CellRendererText()
        releasecolumn = gtk.TreeViewColumn(_('Released'), releasecell, text=EpisodeListModel.C_PUBLISHED_TEXT)
        releasecolumn.set_sort_column_id(EpisodeListModel.C_PUBLISHED)

        namecolumn.set_reorderable(True)
        self.treeAvailable.append_column(namecolumn)

        if not gpodder.ui.maemo:
            for itemcolumn in (sizecolumn, releasecolumn):
                itemcolumn.set_reorderable(True)
                self.treeAvailable.append_column(itemcolumn)

        # Set up type-ahead find for the episode list
        def on_key_press(treeview, event):
            if event.keyval == gtk.keysyms.Escape:
                self.hide_episode_search()
            elif gpodder.ui.fremantle and event.keyval == gtk.keysyms.BackSpace:
                self.hide_episode_search()
            elif event.state & gtk.gdk.CONTROL_MASK:
                # Don't handle type-ahead when control is pressed (so shortcuts
                # with the Ctrl key still work, e.g. Ctrl+A, ...)
                return False
            else:
                unicode_char_id = gtk.gdk.keyval_to_unicode(event.keyval)
                if unicode_char_id == 0:
                    return False
                input_char = unichr(unicode_char_id)
                self.show_episode_search(input_char)
            return True
        self.treeAvailable.connect('key-press-event', on_key_press)

        if gpodder.ui.desktop and not self.config.enable_fingerscroll:
            self.treeAvailable.enable_model_drag_source(gtk.gdk.BUTTON1_MASK, \
                    (('text/uri-list', 0, 0),), gtk.gdk.ACTION_COPY)
            def drag_data_get(tree, context, selection_data, info, timestamp):
                if self.config.on_drag_mark_played:
                    for episode in self.get_selected_episodes():
                        episode.mark(is_played=True)
                    self.on_selected_episodes_status_changed()
                uris = ['file://'+e.local_filename(create=False) \
                        for e in self.get_selected_episodes() \
                        if e.was_downloaded(and_exists=True)]
                uris.append('') # for the trailing '\r\n'
                selection_data.set(selection_data.target, 8, '\r\n'.join(uris))
            self.treeAvailable.connect('drag-data-get', drag_data_get)

        selection = self.treeAvailable.get_selection()
        if self.config.maemo_enable_gestures or self.config.enable_fingerscroll:
            selection.set_mode(gtk.SELECTION_SINGLE)
        elif gpodder.ui.fremantle:
            selection.set_mode(gtk.SELECTION_SINGLE)
        else:
            selection.set_mode(gtk.SELECTION_MULTIPLE)
            # Update the sensitivity of the toolbar buttons on the Desktop
            selection.connect('changed', lambda s: self.play_or_download())
            # show embedded notes
            selection.connect('changed', self.on_treeAvailable_selection_changed)

        if gpodder.ui.diablo:
            # Set up the tap-and-hold context menu for podcasts
            menu = gtk.Menu()
            menu.append(self.itemUpdateChannel.create_menu_item())
            menu.append(self.itemEditChannel.create_menu_item())
            menu.append(gtk.SeparatorMenuItem())
            menu.append(self.itemRemoveChannel.create_menu_item())
            menu.append(gtk.SeparatorMenuItem())
            item = gtk.ImageMenuItem(_('Close this menu'))
            item.set_image(gtk.image_new_from_stock(gtk.STOCK_CLOSE, \
                    gtk.ICON_SIZE_MENU))
            menu.append(item)
            menu.show_all()
            menu = self.set_finger_friendly(menu)
            self.treeChannels.tap_and_hold_setup(menu)


    def init_download_list_treeview(self):
        # enable multiple selection support
        self.treeDownloads.get_selection().set_mode(gtk.SELECTION_MULTIPLE)
        self.treeDownloads.set_search_equal_func(TreeViewHelper.make_search_equal_func(DownloadStatusModel))

        # columns and renderers for "download progress" tab
        # First column: [ICON] Episodename
        column = gtk.TreeViewColumn(_('Episode'))

        cell = gtk.CellRendererPixbuf()
        if gpodder.ui.maemo:
            cell.set_fixed_size(50, 50)
        cell.set_property('stock-size', gtk.ICON_SIZE_BUTTON)
        column.pack_start(cell, expand=False)
        column.add_attribute(cell, 'icon-name', \
                DownloadStatusModel.C_ICON_NAME)

        cell = gtk.CellRendererText()
        cell.set_property('ellipsize', pango.ELLIPSIZE_END)
        column.pack_start(cell, expand=True)
        column.add_attribute(cell, 'markup', DownloadStatusModel.C_NAME)
        column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        column.set_expand(True)
        self.treeDownloads.append_column(column)

        # Second column: Progress
        cell = gtk.CellRendererProgress()
        cell.set_property('yalign', .5)
        cell.set_property('ypad', 6)
        column = gtk.TreeViewColumn(_('Progress'), cell,
                value=DownloadStatusModel.C_PROGRESS, \
                text=DownloadStatusModel.C_PROGRESS_TEXT)
        column.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        column.set_expand(False)
        self.treeDownloads.append_column(column)
        if gpodder.ui.maemo:
            column.set_property('min-width', 200)
            column.set_property('max-width', 200)
        else:
            column.set_property('min-width', 150)
            column.set_property('max-width', 150)

        self.treeDownloads.set_model(self.download_status_model)
        TreeViewHelper.set(self.treeDownloads, TreeViewHelper.ROLE_DOWNLOADS)

    def on_treeview_expose_event(self, treeview, event):
        if event.window == treeview.get_bin_window():
            model = treeview.get_model()
            if (model is not None and model.get_iter_first() is not None):
                return False

            role = getattr(treeview, TreeViewHelper.ROLE, None)
            if role is None:
                return False

            ctx = event.window.cairo_create()
            ctx.rectangle(event.area.x, event.area.y,
                    event.area.width, event.area.height)
            ctx.clip()

            x, y, width, height, depth = event.window.get_geometry()
            progress = None

            if role == TreeViewHelper.ROLE_EPISODES:
                if self.currently_updating:
                    text = _('Loading episodes')
                elif self.config.episode_list_view_mode != \
                        EpisodeListModel.VIEW_ALL:
                    text = _('No episodes in current view')
                else:
                    text = _('No episodes available')
            elif role == TreeViewHelper.ROLE_PODCASTS:
                if self.config.episode_list_view_mode != \
                        EpisodeListModel.VIEW_ALL and \
                        self.config.podcast_list_hide_boring and \
                        len(self.channels) > 0:
                    text = _('No podcasts in this view')
                else:
                    text = _('No subscriptions')
            elif role == TreeViewHelper.ROLE_DOWNLOADS:
                text = _('No active downloads')
            else:
                raise Exception('on_treeview_expose_event: unknown role')

            if gpodder.ui.fremantle:
                from gpodder.gtkui.frmntl import style
                font_desc = style.get_font_desc('LargeSystemFont')
            else:
                font_desc = None

            draw_text_box_centered(ctx, treeview, width, height, text, font_desc, progress)

        return False

    def enable_download_list_update(self):
        if not self.download_list_update_enabled:
            self.update_downloads_list()
            gobject.timeout_add(1500, self.update_downloads_list)
            self.download_list_update_enabled = True

    def cleanup_downloads(self):
        model = self.download_status_model

        all_tasks = [(gtk.TreeRowReference(model, row.path), row[0]) for row in model]
        changed_episode_urls = set()
        for row_reference, task in all_tasks:
            if task.status in (task.DONE, task.CANCELLED):
                model.remove(model.get_iter(row_reference.get_path()))
                try:
                    # We don't "see" this task anymore - remove it;
                    # this is needed, so update_episode_list_icons()
                    # below gets the correct list of "seen" tasks
                    self.download_tasks_seen.remove(task)
                except KeyError, key_error:
                    log('Cannot remove task from "seen" list: %s', task, sender=self)
                changed_episode_urls.add(task.url)
                # Tell the task that it has been removed (so it can clean up)
                task.removed_from_list()

        # Tell the podcasts tab to update icons for our removed podcasts
        self.update_episode_list_icons(changed_episode_urls)

        # Tell the shownotes window that we have removed the episode
        if self.episode_shownotes_window is not None and \
                self.episode_shownotes_window.episode is not None and \
                self.episode_shownotes_window.episode.url in changed_episode_urls:
            self.episode_shownotes_window._download_status_changed(None)

        # Update the downloads list one more time
        self.update_downloads_list(can_call_cleanup=False)

    def on_tool_downloads_toggled(self, toolbutton):
        if toolbutton.get_active():
            self.wNotebook.set_current_page(1)
        else:
            self.wNotebook.set_current_page(0)

    def add_download_task_monitor(self, monitor):
        self.download_task_monitors.add(monitor)
        model = self.download_status_model
        if model is None:
            model = ()
        for row in model:
            task = row[self.download_status_model.C_TASK]
            monitor.task_updated(task)

    def remove_download_task_monitor(self, monitor):
        self.download_task_monitors.remove(monitor)

    def update_downloads_list(self, can_call_cleanup=True):
        try:
            model = self.download_status_model

            downloading, failed, finished, queued, paused, others = 0, 0, 0, 0, 0, 0
            total_speed, total_size, done_size = 0, 0, 0

            # Keep a list of all download tasks that we've seen
            download_tasks_seen = set()

            # Remember the DownloadTask object for the episode that
            # has been opened in the episode shownotes dialog (if any)
            if self.episode_shownotes_window is not None:
                shownotes_episode = self.episode_shownotes_window.episode
                shownotes_task = None
            else:
                shownotes_episode = None
                shownotes_task = None

            # Do not go through the list of the model is not (yet) available
            if model is None:
                model = ()

            for row in model:
                self.download_status_model.request_update(row.iter)

                task = row[self.download_status_model.C_TASK]
                speed, size, status, progress = task.speed, task.total_size, task.status, task.progress

                # Let the download task monitors know of changes
                for monitor in self.download_task_monitors:
                    monitor.task_updated(task)

                total_size += size
                done_size += size*progress

                if shownotes_episode is not None and \
                        shownotes_episode.url == task.episode.url:
                    shownotes_task = task

                download_tasks_seen.add(task)

                if status == download.DownloadTask.DOWNLOADING:
                    downloading += 1
                    total_speed += speed
                elif status == download.DownloadTask.FAILED:
                    failed += 1
                elif status == download.DownloadTask.DONE:
                    finished += 1
                elif status == download.DownloadTask.QUEUED:
                    queued += 1
                elif status == download.DownloadTask.PAUSED:
                    paused += 1
                else:
                    others += 1

            # Remember which tasks we have seen after this run
            self.download_tasks_seen = download_tasks_seen

            if gpodder.ui.desktop:
                text = [_('Downloads')]
                if downloading + failed + queued > 0:
                    s = []
                    if downloading > 0:
                        s.append(N_('%(count)d active', '%(count)d active', downloading) % {'count':downloading})
                    if failed > 0:
                        s.append(N_('%(count)d failed', '%(count)d failed', failed) % {'count':failed})
                    if queued > 0:
                        s.append(N_('%(count)d queued', '%(count)d queued', queued) % {'count':queued})
                    text.append(' (' + ', '.join(s)+')')
                self.labelDownloads.set_text(''.join(text))
            elif gpodder.ui.diablo:
                sum = downloading + failed + finished + queued + paused + others
                if sum:
                    self.tool_downloads.set_label(_('Downloads (%d)') % sum)
                else:
                    self.tool_downloads.set_label(_('Downloads'))
            elif gpodder.ui.fremantle:
                if downloading + queued > 0:
                    self.button_downloads.set_value(N_('%(count)d active', '%(count)d active', downloading+queued) % {'count':(downloading+queued)})
                elif failed > 0:
                    self.button_downloads.set_value(N_('%(count)d failed', '%(count)d failed', failed) % {'count':failed})
                elif paused > 0:
                    self.button_downloads.set_value(N_('%(count)d paused', '%(count)d paused', paused) % {'count':paused})
                else:
                    self.button_downloads.set_value(_('Idle'))

            title = [self.default_title]

            # We have to update all episodes/channels for which the status has
            # changed. Accessing task.status_changed has the side effect of
            # re-setting the changed flag, so we need to get the "changed" list
            # of tuples first and split it into two lists afterwards
            changed = [(task.url, task.podcast_url) for task in \
                    self.download_tasks_seen if task.status_changed]
            episode_urls = [episode_url for episode_url, channel_url in changed]
            channel_urls = [channel_url for episode_url, channel_url in changed]

            count = downloading + queued
            if count > 0:
                title.append(N_('downloading %(count)d file', 'downloading %(count)d files', count) % {'count':count})

                if total_size > 0:
                    percentage = 100.0*done_size/total_size
                else:
                    percentage = 0.0
                total_speed = util.format_filesize(total_speed)
                title[1] += ' (%d%%, %s/s)' % (percentage, total_speed)
                if self.tray_icon is not None:
                    # Update the tray icon status and progress bar
                    self.tray_icon.set_status(self.tray_icon.STATUS_DOWNLOAD_IN_PROGRESS, title[1])
                    self.tray_icon.draw_progress_bar(percentage/100.)
            else:
                if self.tray_icon is not None:
                    # Update the tray icon status
                    self.tray_icon.set_status()
                if gpodder.ui.desktop:
                    self.downloads_finished(self.download_tasks_seen)
                if gpodder.ui.diablo:
                    hildon.hildon_banner_show_information(self.gPodder, '', 'gPodder: %s' % _('All downloads finished'))
                log('All downloads have finished.', sender=self)
                if self.config.cmd_all_downloads_complete:
                    util.run_external_command(self.config.cmd_all_downloads_complete)

                if gpodder.ui.fremantle:
                    message = '\n'.join(['%s: %s' % (str(task), \
                            task.error_message) for task in self.download_tasks_seen if task.notify_as_failed()])
                    if message:
                        self.show_message(message, _('Downloads failed'), important=True)

                # Remove finished episodes
                if self.config.auto_cleanup_downloads and can_call_cleanup:
                    self.cleanup_downloads()

                # Stop updating the download list here
                self.download_list_update_enabled = False

            if not gpodder.ui.fremantle:
                self.gPodder.set_title(' - '.join(title))

            self.update_episode_list_icons(episode_urls)
            if self.episode_shownotes_window is not None:
                if (shownotes_task and shownotes_task.url in episode_urls) or \
                        shownotes_task != self.episode_shownotes_window.task:
                    self.episode_shownotes_window._download_status_changed(shownotes_task)
                self.episode_shownotes_window._download_status_progress()
            self.play_or_download()
            if channel_urls:
                self.update_podcast_list_model(channel_urls)

            return self.download_list_update_enabled
        except Exception, e:
            log('Exception happened while updating download list.', sender=self, traceback=True)
            self.show_message('%s\n\n%s' % (_('Please report this problem and restart gPodder:'), str(e)), _('Unhandled exception'), important=True)
            # We return False here, so the update loop won't be called again,
            # that's why we require the restart of gPodder in the message.
            return False

    def on_config_changed(self, *args):
        util.idle_add(self._on_config_changed, *args)

    def _on_config_changed(self, name, old_value, new_value):
        if name == 'show_toolbar' and gpodder.ui.desktop:
            self.toolbar.set_property('visible', new_value)
        elif name == 'episode_list_descriptions':
            self.update_episode_list_model()
        elif name == 'episode_list_thumbnails':
            self.update_episode_list_icons(all=True)
        elif name == 'rotation_mode':
            self._fremantle_rotation.set_mode(new_value)
        elif name in ('auto_update_feeds', 'auto_update_frequency'):
            self.restart_auto_update_timer()
        elif name == 'podcast_list_view_all' or name == 'podcast_list_view_new':
            # Force a update of the podcast list model
            self.channel_list_changed = True
            if gpodder.ui.fremantle:
                hildon.hildon_gtk_window_set_progress_indicator(self.main_window, True)
                while gtk.events_pending():
                    gtk.main_iteration(False)
            self.update_podcast_list_model()
            if gpodder.ui.fremantle:
                hildon.hildon_gtk_window_set_progress_indicator(self.main_window, False)

    def on_treeview_query_tooltip(self, treeview, x, y, keyboard_tooltip, tooltip):
        # With get_bin_window, we get the window that contains the rows without
        # the header. The Y coordinate of this window will be the height of the
        # treeview header. This is the amount we have to subtract from the
        # event's Y coordinate to get the coordinate to pass to get_path_at_pos
        (x_bin, y_bin) = treeview.get_bin_window().get_position()
        y -= x_bin
        y -= y_bin
        (path, column, rx, ry) = treeview.get_path_at_pos( x, y) or (None,)*4

        if not getattr(treeview, TreeViewHelper.CAN_TOOLTIP) or x > 50 or (column is not None and column != treeview.get_columns()[0]):
            setattr(treeview, TreeViewHelper.LAST_TOOLTIP, None)
            return False

        if path is not None:
            model = treeview.get_model()
            iter = model.get_iter(path)
            role = getattr(treeview, TreeViewHelper.ROLE)

            if role == TreeViewHelper.ROLE_EPISODES:
                id = model.get_value(iter, EpisodeListModel.C_URL)
            elif role == TreeViewHelper.ROLE_PODCASTS:
                id = model.get_value(iter, PodcastListModel.C_URL)

            last_tooltip = getattr(treeview, TreeViewHelper.LAST_TOOLTIP)
            if last_tooltip is not None and last_tooltip != id:
                setattr(treeview, TreeViewHelper.LAST_TOOLTIP, None)
                return False
            setattr(treeview, TreeViewHelper.LAST_TOOLTIP, id)

            if role == TreeViewHelper.ROLE_EPISODES:
                description = model.get_value(iter, EpisodeListModel.C_TOOLTIP)
                if description:
                    tooltip.set_text(description)
                else:
                    return False
            elif role == TreeViewHelper.ROLE_PODCASTS:
                channel = model.get_value(iter, PodcastListModel.C_CHANNEL)
                if channel is None:
                    return False
                channel.request_save_dir_size()
                diskspace_str = util.format_filesize(channel.save_dir_size, 0)
                error_str = model.get_value(iter, PodcastListModel.C_ERROR)
                if error_str:
                    error_str = _('Feedparser error: %s') % saxutils.escape(error_str.strip())
                    error_str = '<span foreground="#ff0000">%s</span>' % error_str
                table = gtk.Table(rows=3, columns=3)
                table.set_row_spacings(5)
                table.set_col_spacings(5)
                table.set_border_width(5)

                heading = gtk.Label()
                heading.set_alignment(0, 1)
                heading.set_markup('<b><big>%s</big></b>\n<small>%s</small>' % (saxutils.escape(channel.title), saxutils.escape(channel.url)))
                table.attach(heading, 0, 1, 0, 1)
                size_info = gtk.Label()
                size_info.set_alignment(1, 1)
                size_info.set_justify(gtk.JUSTIFY_RIGHT)
                size_info.set_markup('<b>%s</b>\n<small>%s</small>' % (diskspace_str, _('disk usage')))
                table.attach(size_info, 2, 3, 0, 1)

                table.attach(gtk.HSeparator(), 0, 3, 1, 2)

                if len(channel.description) < 500:
                    description = channel.description
                else:
                    pos = channel.description.find('\n\n')
                    if pos == -1 or pos > 500:
                        description = channel.description[:498]+'[...]'
                    else:
                        description = channel.description[:pos]

                description = gtk.Label(description)
                if error_str:
                    description.set_markup(error_str)
                description.set_alignment(0, 0)
                description.set_line_wrap(True)
                table.attach(description, 0, 3, 2, 3)

                table.show_all()
                tooltip.set_custom(table)

            return True

        setattr(treeview, TreeViewHelper.LAST_TOOLTIP, None)
        return False

    def treeview_allow_tooltips(self, treeview, allow):
        setattr(treeview, TreeViewHelper.CAN_TOOLTIP, allow)

    def update_m3u_playlist_clicked(self, widget):
        if self.active_channel is not None:
            self.active_channel.update_m3u_playlist()
            self.show_message(_('Updated M3U playlist in download folder.'), _('Updated playlist'), widget=self.treeChannels)

    def treeview_handle_context_menu_click(self, treeview, event):
        x, y = int(event.x), int(event.y)
        path, column, rx, ry = treeview.get_path_at_pos(x, y) or (None,)*4

        selection = treeview.get_selection()
        model, paths = selection.get_selected_rows()

        if path is None or (path not in paths and \
                event.button == self.context_menu_mouse_button):
            # We have right-clicked, but not into the selection,
            # assume we don't want to operate on the selection
            paths = []

        if path is not None and not paths and \
                event.button == self.context_menu_mouse_button:
            # No selection or clicked outside selection;
            # select the single item where we clicked
            treeview.grab_focus()
            treeview.set_cursor(path, column, 0)
            paths = [path]

        if not paths:
            # Unselect any remaining items (clicked elsewhere)
            if hasattr(treeview, 'is_rubber_banding_active'):
                if not treeview.is_rubber_banding_active():
                    selection.unselect_all()
            else:
                selection.unselect_all()

        return model, paths

    def downloads_list_get_selection(self, model=None, paths=None):
        if model is None and paths is None:
            selection = self.treeDownloads.get_selection()
            model, paths = selection.get_selected_rows()

        can_queue, can_cancel, can_pause, can_remove, can_force = (True,)*5
        selected_tasks = [(gtk.TreeRowReference(model, path), \
                           model.get_value(model.get_iter(path), \
                           DownloadStatusModel.C_TASK)) for path in paths]

        for row_reference, task in selected_tasks:
            if task.status != download.DownloadTask.QUEUED:
                can_force = False
            if task.status not in (download.DownloadTask.PAUSED, \
                    download.DownloadTask.FAILED, \
                    download.DownloadTask.CANCELLED):
                can_queue = False
            if task.status not in (download.DownloadTask.PAUSED, \
                    download.DownloadTask.QUEUED, \
                    download.DownloadTask.DOWNLOADING, \
                    download.DownloadTask.FAILED):
                can_cancel = False
            if task.status not in (download.DownloadTask.QUEUED, \
                    download.DownloadTask.DOWNLOADING):
                can_pause = False
            if task.status not in (download.DownloadTask.CANCELLED, \
                    download.DownloadTask.FAILED, \
                    download.DownloadTask.DONE):
                can_remove = False

        return selected_tasks, can_queue, can_cancel, can_pause, can_remove, can_force

    def downloads_finished(self, download_tasks_seen):
        finished_downloads = [str(task) for task in download_tasks_seen if task.notify_as_finished()]
        failed_downloads = [str(task)+' ('+task.error_message+')' for task in download_tasks_seen if task.notify_as_failed()]

        if finished_downloads and failed_downloads:
            message = self.format_episode_list(finished_downloads, 5)
            message += '\n\n<i>%s</i>\n' % _('These downloads failed:')
            message += self.format_episode_list(failed_downloads, 5)
            self.show_message(message, _('Downloads finished'), True, widget=self.labelDownloads)
        elif finished_downloads:
            message = self.format_episode_list(finished_downloads)
            self.show_message(message, _('Downloads finished'), widget=self.labelDownloads)
        elif failed_downloads:
            message = self.format_episode_list(failed_downloads)
            self.show_message(message, _('Downloads failed'), True, widget=self.labelDownloads)

        # Open torrent files right after download (bug 1029)
        if self.config.open_torrent_after_download:
            for task in download_tasks_seen:
                if task.status != task.DONE:
                    continue

                episode = task.episode
                if episode.mimetype != 'application/x-bittorrent':
                    continue

                self.playback_episodes([episode])


    def format_episode_list(self, episode_list, max_episodes=10):
        """
        Format a list of episode names for notifications

        Will truncate long episode names and limit the amount of
        episodes displayed (max_episodes=10).

        The episode_list parameter should be a list of strings.
        """
        MAX_TITLE_LENGTH = 100

        result = []
        for title in episode_list[:min(len(episode_list), max_episodes)]:
            if len(title) > MAX_TITLE_LENGTH:
                middle = (MAX_TITLE_LENGTH/2)-2
                title = '%s...%s' % (title[0:middle], title[-middle:])
            result.append(saxutils.escape(title))
            result.append('\n')

        more_episodes = len(episode_list) - max_episodes
        if more_episodes > 0:
            result.append('(...')
            result.append(N_('%(count)d more episode', '%(count)d more episodes', more_episodes) % {'count':more_episodes})
            result.append('...)')

        return (''.join(result)).strip()

    def _for_each_task_set_status(self, tasks, status, force_start=False):
        episode_urls = set()
        model = self.treeDownloads.get_model()
        for row_reference, task in tasks:
            if status == download.DownloadTask.QUEUED:
                # Only queue task when its paused/failed/cancelled (or forced)
                if task.status in (task.PAUSED, task.FAILED, task.CANCELLED) or force_start:
                    self.download_queue_manager.add_task(task, force_start)
                    self.enable_download_list_update()
            elif status == download.DownloadTask.CANCELLED:
                # Cancelling a download allowed when downloading/queued
                if task.status in (task.QUEUED, task.DOWNLOADING):
                    task.status = status
                # Cancelling paused/failed downloads requires a call to .run()
                elif task.status in (task.PAUSED, task.FAILED):
                    task.status = status
                    # Call run, so the partial file gets deleted
                    task.run()
            elif status == download.DownloadTask.PAUSED:
                # Pausing a download only when queued/downloading
                if task.status in (task.DOWNLOADING, task.QUEUED):
                    task.status = status
            elif status is None:
                # Remove the selected task - cancel downloading/queued tasks
                if task.status in (task.QUEUED, task.DOWNLOADING):
                    task.status = task.CANCELLED
                model.remove(model.get_iter(row_reference.get_path()))
                # Remember the URL, so we can tell the UI to update
                try:
                    # We don't "see" this task anymore - remove it;
                    # this is needed, so update_episode_list_icons()
                    # below gets the correct list of "seen" tasks
                    self.download_tasks_seen.remove(task)
                except KeyError, key_error:
                    log('Cannot remove task from "seen" list: %s', task, sender=self)
                episode_urls.add(task.url)
                # Tell the task that it has been removed (so it can clean up)
                task.removed_from_list()
            else:
                # We can (hopefully) simply set the task status here
                task.status = status
        # Tell the podcasts tab to update icons for our removed podcasts
        self.update_episode_list_icons(episode_urls)
        # Update the tab title and downloads list
        self.update_downloads_list()

    def treeview_downloads_show_context_menu(self, treeview, event):
        model, paths = self.treeview_handle_context_menu_click(treeview, event)
        if not paths:
            if not hasattr(treeview, 'is_rubber_banding_active'):
                return True
            else:
                return not treeview.is_rubber_banding_active()

        if event.button == self.context_menu_mouse_button:
            selected_tasks, can_queue, can_cancel, can_pause, can_remove, can_force = \
                    self.downloads_list_get_selection(model, paths)

            def make_menu_item(label, stock_id, tasks, status, sensitive, force_start=False):
                # This creates a menu item for selection-wide actions
                item = gtk.ImageMenuItem(label)
                item.set_image(gtk.image_new_from_stock(stock_id, gtk.ICON_SIZE_MENU))
                item.connect('activate', lambda item: self._for_each_task_set_status(tasks, status, force_start))
                item.set_sensitive(sensitive)
                return self.set_finger_friendly(item)

            menu = gtk.Menu()

            item = gtk.ImageMenuItem(_('Episode details'))
            item.set_image(gtk.image_new_from_stock(gtk.STOCK_INFO, gtk.ICON_SIZE_MENU))
            if len(selected_tasks) == 1:
                row_reference, task = selected_tasks[0]
                episode = task.episode
                item.connect('activate', lambda item: self.show_episode_shownotes(episode))
            else:
                item.set_sensitive(False)
            menu.append(self.set_finger_friendly(item))
            menu.append(gtk.SeparatorMenuItem())
            if can_force:
                menu.append(make_menu_item(_('Start download now'), gtk.STOCK_GO_DOWN, selected_tasks, download.DownloadTask.QUEUED, True, True))
            else:
                menu.append(make_menu_item(_('Download'), gtk.STOCK_GO_DOWN, selected_tasks, download.DownloadTask.QUEUED, can_queue, False))
            menu.append(make_menu_item(_('Cancel'), gtk.STOCK_CANCEL, selected_tasks, download.DownloadTask.CANCELLED, can_cancel))
            menu.append(make_menu_item(_('Pause'), gtk.STOCK_MEDIA_PAUSE, selected_tasks, download.DownloadTask.PAUSED, can_pause))
            menu.append(gtk.SeparatorMenuItem())
            menu.append(make_menu_item(_('Remove from list'), gtk.STOCK_REMOVE, selected_tasks, None, can_remove))

            if gpodder.ui.maemo or self.config.enable_fingerscroll:
                # Because we open the popup on left-click for Maemo,
                # we also include a non-action to close the menu
                menu.append(gtk.SeparatorMenuItem())
                item = gtk.ImageMenuItem(_('Close this menu'))
                item.set_image(gtk.image_new_from_stock(gtk.STOCK_CLOSE, gtk.ICON_SIZE_MENU))

                menu.append(self.set_finger_friendly(item))

            menu.show_all()
            menu.popup(None, None, None, event.button, event.time)
            return True

    def treeview_channels_show_context_menu(self, treeview, event):
        model, paths = self.treeview_handle_context_menu_click(treeview, event)
        if not paths:
            return True

        # Check for valid channel id, if there's no id then
        # assume that it is a proxy channel or equivalent
        # and cannot be operated with right click
        if self.active_channel.id is None:
            return True

        if event.button == 3:
            menu = gtk.Menu()

            ICON = lambda x: x

            item = gtk.ImageMenuItem( _('Update podcast'))
            item.set_image(gtk.image_new_from_stock(gtk.STOCK_REFRESH, gtk.ICON_SIZE_MENU))
            item.connect('activate', self.on_itemUpdateChannel_activate)
            item.set_sensitive(not self.updating_feed_cache)
            menu.append(item)

            menu.append(gtk.SeparatorMenuItem())

            item = gtk.CheckMenuItem(_('Keep episodes'))
            item.set_active(self.active_channel.channel_is_locked)
            item.connect('activate', self.on_channel_toggle_lock_activate)
            menu.append(self.set_finger_friendly(item))

            item = gtk.ImageMenuItem(_('Remove podcast'))
            item.set_image(gtk.image_new_from_stock(gtk.STOCK_DELETE, gtk.ICON_SIZE_MENU))
            item.connect( 'activate', self.on_itemRemoveChannel_activate)
            menu.append( item)

            if self.config.device_type != 'none':
                item = gtk.MenuItem(_('Synchronize to device'))
                item.connect('activate', lambda item: self.on_sync_to_ipod_activate(item, self.active_channel.get_downloaded_episodes(), force_played=False))
                menu.append(item)

            menu.append( gtk.SeparatorMenuItem())

            item = gtk.ImageMenuItem(_('Podcast details'))
            item.set_image(gtk.image_new_from_stock(gtk.STOCK_INFO, gtk.ICON_SIZE_MENU))
            item.connect('activate', self.on_itemEditChannel_activate)
            menu.append(item)

            menu.show_all()
            # Disable tooltips while we are showing the menu, so 
            # the tooltip will not appear over the menu
            self.treeview_allow_tooltips(self.treeChannels, False)
            menu.connect('deactivate', lambda menushell: self.treeview_allow_tooltips(self.treeChannels, True))
            menu.popup( None, None, None, event.button, event.time)

            return True

    def on_itemClose_activate(self, widget):
        if self.tray_icon is not None:
            self.iconify_main_window()
        else:
            self.on_gPodder_delete_event(widget)

    def cover_file_removed(self, channel_url):
        """
        The Cover Downloader calls this when a previously-
        available cover has been removed from the disk. We
        have to update our model to reflect this change.
        """
        self.podcast_list_model.delete_cover_by_url(channel_url)
    
    def cover_download_finished(self, channel, pixbuf):
        """
        The Cover Downloader calls this when it has finished
        downloading (or registering, if already downloaded)
        a new channel cover, which is ready for displaying.
        """
        self.podcast_list_model.add_cover_by_channel(channel, pixbuf)

    def save_episodes_as_file(self, episodes):
        for episode in episodes:
            self.save_episode_as_file(episode)

    def save_episode_as_file(self, episode):
        PRIVATE_FOLDER_ATTRIBUTE = '_save_episodes_as_file_folder'
        if episode.was_downloaded(and_exists=True):
            folder = getattr(self, PRIVATE_FOLDER_ATTRIBUTE, None)
            copy_from = episode.local_filename(create=False)
            assert copy_from is not None
            copy_to = util.sanitize_filename(episode.sync_filename(\
                    self.config.custom_sync_name_enabled, \
                    self.config.custom_sync_name))
            (result, folder) = self.show_copy_dialog(src_filename=copy_from, dst_filename=copy_to, dst_directory=folder)
            setattr(self, PRIVATE_FOLDER_ATTRIBUTE, folder)

    def copy_episodes_bluetooth(self, episodes):
        episodes_to_copy = [e for e in episodes if e.was_downloaded(and_exists=True)]

        if gpodder.ui.maemo:
            util.bluetooth_send_files_maemo([e.local_filename(create=False) \
                    for e in episodes_to_copy])
            return True

        def convert_and_send_thread(episode):
            for episode in episodes:
                filename = episode.local_filename(create=False)
                assert filename is not None
                destfile = os.path.join(tempfile.gettempdir(), \
                        util.sanitize_filename(episode.sync_filename(self.config.custom_sync_name_enabled, self.config.custom_sync_name)))
                (base, ext) = os.path.splitext(filename)
                if not destfile.endswith(ext):
                    destfile += ext

                try:
                    shutil.copyfile(filename, destfile)
                    util.bluetooth_send_file(destfile)
                except:
                    log('Cannot copy "%s" to "%s".', filename, destfile, sender=self)
                    self.notification(_('Error converting file.'), _('Bluetooth file transfer'), important=True)

                util.delete_file(destfile)

        threading.Thread(target=convert_and_send_thread, args=[episodes_to_copy]).start()

    def get_device_name(self):
        if self.config.device_type == 'ipod':
            return _('iPod')
        elif self.config.device_type in ('filesystem', 'mtp'):
            return _('MP3 player')
        else:
            return '(unknown device)'

    def _treeview_button_released(self, treeview, event):
        xpos, ypos = TreeViewHelper.get_button_press_event(treeview)
        dy = int(abs(event.y-ypos))
        dx = int(event.x-xpos)

        selection = treeview.get_selection()
        path = treeview.get_path_at_pos(int(event.x), int(event.y))
        if path is None or dy > 30:
            return (False, dx, dy)

        path, column, x, y = path
        selection.select_path(path)
        treeview.set_cursor(path)
        treeview.grab_focus()

        return (True, dx, dy)

    def treeview_channels_handle_gestures(self, treeview, event):
        if self.currently_updating:
            return False

        selected, dx, dy = self._treeview_button_released(treeview, event)

        if selected:
            if self.config.maemo_enable_gestures:
                if dx > 70:
                    self.on_itemUpdateChannel_activate()
                elif dx < -70:
                    self.on_itemEditChannel_activate(treeview)

        return False

    def treeview_available_handle_gestures(self, treeview, event):
        selected, dx, dy = self._treeview_button_released(treeview, event)

        if selected:
            if self.config.maemo_enable_gestures:
                if dx > 70:
                    self.on_playback_selected_episodes(None)
                    return True
                elif dx < -70:
                    self.on_shownotes_selected_episodes(None)
                    return True

            # Pass the event to the context menu handler for treeAvailable
            self.treeview_available_show_context_menu(treeview, event)

        return True

    def treeview_available_show_context_menu(self, treeview, event):
        model, paths = self.treeview_handle_context_menu_click(treeview, event)
        if not paths:
            if not hasattr(treeview, 'is_rubber_banding_active'):
                return True
            else:
                return not treeview.is_rubber_banding_active()

        if event.button == self.context_menu_mouse_button:
            episodes = self.get_selected_episodes()
            any_locked = any(e.is_locked for e in episodes)
            any_played = any(e.is_played for e in episodes)
            one_is_new = any(e.state == gpodder.STATE_NORMAL and not e.is_played for e in episodes)
            downloaded = all(e.was_downloaded(and_exists=True) for e in episodes)
            downloading = any(self.episode_is_downloading(e) for e in episodes)

            menu = gtk.Menu()

            (can_play, can_download, can_transfer, can_cancel, can_delete, open_instead_of_play) = self.play_or_download()

            if open_instead_of_play:
                item = gtk.ImageMenuItem(gtk.STOCK_OPEN)
            elif downloaded:
                item = gtk.ImageMenuItem(gtk.STOCK_MEDIA_PLAY)
            else:
                item = gtk.ImageMenuItem(_('Stream'))
                item.set_image(gtk.image_new_from_stock(gtk.STOCK_MEDIA_PLAY, gtk.ICON_SIZE_MENU))

            item.set_sensitive(can_play and not downloading)
            item.connect('activate', self.on_playback_selected_episodes)
            menu.append(self.set_finger_friendly(item))

            if not can_cancel:
                item = gtk.ImageMenuItem(_('Download'))
                item.set_image(gtk.image_new_from_stock(gtk.STOCK_GO_DOWN, gtk.ICON_SIZE_MENU))
                item.set_sensitive(can_download)
                item.connect('activate', self.on_download_selected_episodes)
                menu.append(self.set_finger_friendly(item))
            else:
                item = gtk.ImageMenuItem(gtk.STOCK_CANCEL)
                item.connect('activate', self.on_item_cancel_download_activate)
                menu.append(self.set_finger_friendly(item))

            item = gtk.ImageMenuItem(gtk.STOCK_DELETE)
            item.set_sensitive(can_delete)
            item.connect('activate', self.on_btnDownloadedDelete_clicked)
            menu.append(self.set_finger_friendly(item))

            ICON = lambda x: x

            # Ok, this probably makes sense to only display for downloaded files
            if downloaded:
                menu.append(gtk.SeparatorMenuItem())
                share_item = gtk.MenuItem(_('Send to'))
                menu.append(self.set_finger_friendly(share_item))
                share_menu = gtk.Menu()

                item = gtk.ImageMenuItem(_('Local folder'))
                item.set_image(gtk.image_new_from_stock(gtk.STOCK_DIRECTORY, gtk.ICON_SIZE_MENU))
                item.connect('button-press-event', lambda w, ee: self.save_episodes_as_file(episodes))
                share_menu.append(self.set_finger_friendly(item))
                if self.bluetooth_available:
                    item = gtk.ImageMenuItem(_('Bluetooth device'))
                    if gpodder.ui.maemo:
                        icon_name = ICON('qgn_list_filesys_bluetooth')
                    else:
                        icon_name = ICON('bluetooth')
                    item.set_image(gtk.image_new_from_icon_name(icon_name, gtk.ICON_SIZE_MENU))
                    item.connect('button-press-event', lambda w, ee: self.copy_episodes_bluetooth(episodes))
                    share_menu.append(self.set_finger_friendly(item))
                if can_transfer:
                    item = gtk.ImageMenuItem(self.get_device_name())
                    item.set_image(gtk.image_new_from_icon_name(ICON('multimedia-player'), gtk.ICON_SIZE_MENU))
                    item.connect('button-press-event', lambda w, ee: self.on_sync_to_ipod_activate(w, episodes))
                    share_menu.append(self.set_finger_friendly(item))

                share_item.set_submenu(share_menu)

            if not downloading:
                menu.append(gtk.SeparatorMenuItem())
                if one_is_new:
                    item = gtk.CheckMenuItem(_('New'))
                    item.set_active(True)
                    item.connect('activate', lambda w: self.mark_selected_episodes_old())
                    menu.append(self.set_finger_friendly(item))
                else:
                    item = gtk.CheckMenuItem(_('New'))
                    item.set_active(False)
                    item.connect('activate', lambda w: self.mark_selected_episodes_new())
                    menu.append(self.set_finger_friendly(item))

            if downloaded:
                    item = gtk.CheckMenuItem(_('Played'))
                    item.set_active(any_played)
                    item.connect( 'activate', lambda w: self.on_item_toggle_played_activate( w, False, not any_played))
                    menu.append(self.set_finger_friendly(item))

                    item = gtk.CheckMenuItem(_('Keep episode'))
                    item.set_active(any_locked)
                    item.connect('activate', lambda w: self.on_item_toggle_lock_activate( w, False, not any_locked))
                    menu.append(self.set_finger_friendly(item))

            menu.append(gtk.SeparatorMenuItem())
            # Single item, add episode information menu item
            item = gtk.ImageMenuItem(_('Episode details'))
            item.set_image(gtk.image_new_from_stock( gtk.STOCK_INFO, gtk.ICON_SIZE_MENU))
            item.connect('activate', lambda w: self.show_episode_shownotes(episodes[0]))
            menu.append(self.set_finger_friendly(item))

            if gpodder.ui.maemo or self.config.enable_fingerscroll:
                # Because we open the popup on left-click for Maemo,
                # we also include a non-action to close the menu
                menu.append(gtk.SeparatorMenuItem())
                item = gtk.ImageMenuItem(_('Close this menu'))
                item.set_image(gtk.image_new_from_stock(gtk.STOCK_CLOSE, gtk.ICON_SIZE_MENU))
                menu.append(self.set_finger_friendly(item))

            menu.show_all()
            # Disable tooltips while we are showing the menu, so 
            # the tooltip will not appear over the menu
            self.treeview_allow_tooltips(self.treeAvailable, False)
            menu.connect('deactivate', lambda menushell: self.treeview_allow_tooltips(self.treeAvailable, True))
            menu.popup( None, None, None, event.button, event.time)

            return True

    def set_title(self, new_title):
        if not gpodder.ui.fremantle:
            self.default_title = new_title
            self.gPodder.set_title(new_title)

    def update_episode_list_icons(self, urls=None, selected=False, all=False):
        """
        Updates the status icons in the episode list.

        If urls is given, it should be a list of URLs
        of episodes that should be updated.

        If urls is None, set ONE OF selected, all to
        True (the former updates just the selected
        episodes and the latter updates all episodes).
        """
        additional_args = (self.episode_is_downloading, \
                self.config.episode_list_descriptions and gpodder.ui.desktop, \
                self.config.episode_list_thumbnails and gpodder.ui.desktop)

        if urls is not None:
            # We have a list of URLs to walk through
            self.episode_list_model.update_by_urls(urls, *additional_args)
        elif selected and not all:
            # We should update all selected episodes
            selection = self.treeAvailable.get_selection()
            model, paths = selection.get_selected_rows()
            for path in reversed(paths):
                iter = model.get_iter(path)
                self.episode_list_model.update_by_filter_iter(iter, \
                        *additional_args)
        elif all and not selected:
            # We update all (even the filter-hidden) episodes
            self.episode_list_model.update_all(*additional_args)
        else:
            # Wrong/invalid call - have to specify at least one parameter
            raise ValueError('Invalid call to update_episode_list_icons')

    def episode_list_status_changed(self, episodes):
        self.update_episode_list_icons(set(e.url for e in episodes))
        self.update_podcast_list_model(set(e.channel.url for e in episodes))
        self.db.commit()

    def clean_up_downloads(self, delete_partial=False):
        # Clean up temporary files left behind by old gPodder versions
        temporary_files = glob.glob('%s/*/.tmp-*' % self.config.download_dir)

        if delete_partial:
            temporary_files += glob.glob('%s/*/*.partial' % self.config.download_dir)

        for tempfile in temporary_files:
            util.delete_file(tempfile)

        # Clean up empty download folders and abandoned download folders
        download_dirs = glob.glob(os.path.join(self.config.download_dir, '*'))
        for ddir in download_dirs:
            if os.path.isdir(ddir) and False: # FIXME not db.channel_foldername_exists(os.path.basename(ddir)):
                globr = glob.glob(os.path.join(ddir, '*'))
                if len(globr) == 0 or (len(globr) == 1 and globr[0].endswith('/cover')):
                    log('Stale download directory found: %s', os.path.basename(ddir), sender=self)
                    shutil.rmtree(ddir, ignore_errors=True)

    def streaming_possible(self):
        if gpodder.ui.desktop:
            # User has to have a media player set on the Desktop, or else we
            # would probably open the browser when giving a URL to xdg-open..
            return (self.config.player and self.config.player != 'default')
        elif gpodder.ui.maemo:
            # On Maemo, the default is to use the Nokia Media Player, which is
            # already able to deal with HTTP URLs the right way, so we
            # unconditionally enable streaming always on Maemo
            return True

        return False

    def playback_episodes_for_real(self, episodes):
        groups = collections.defaultdict(list)
        for episode in episodes:
            file_type = episode.file_type()
            if file_type == 'video' and self.config.videoplayer and \
                    self.config.videoplayer != 'default':
                player = self.config.videoplayer
                if gpodder.ui.diablo:
                    # Use the wrapper script if it's installed to crop 3GP YouTube
                    # videos to fit the screen (looks much nicer than w/ black border)
                    if player == 'mplayer' and util.find_command('gpodder-mplayer'):
                        player = 'gpodder-mplayer'
                elif gpodder.ui.fremantle and player == 'mplayer':
                    player = 'mplayer -fs %F'
            elif file_type == 'audio' and self.config.player and \
                    self.config.player != 'default':
                player = self.config.player
            else:
                player = 'default'

            # Mark episode as played in the database
            episode.mark(is_played=True)
            self.mygpo_client.on_playback([episode])

            filename = episode.local_filename(create=False)
            if filename is None or not os.path.exists(filename):
                filename = episode.url
                if youtube.is_video_link(filename):
                    fmt_id = self.config.youtube_preferred_fmt_id
                    if gpodder.ui.fremantle:
                        fmt_id = 5
                    filename = youtube.get_real_download_url(filename, fmt_id)

            # Determine the playback resume position - if the file
            # was played 100%, we simply start from the beginning
            resume_position = episode.current_position
            if resume_position == episode.total_time:
                resume_position = 0

            # Only on Maemo 5, and only if the episode isn't finished yet
            if gpodder.ui.fremantle and not episode.is_finished():
                self.mafw_monitor.set_resume_point(filename, resume_position)

            # If Panucci is configured, use D-Bus on Maemo to call it
            if player == 'panucci':
                try:
                    PANUCCI_NAME = 'org.panucci.panucciInterface'
                    PANUCCI_PATH = '/panucciInterface'
                    PANUCCI_INTF = 'org.panucci.panucciInterface'
                    o = gpodder.dbus_session_bus.get_object(PANUCCI_NAME, PANUCCI_PATH)
                    i = dbus.Interface(o, PANUCCI_INTF)

                    def on_reply(*args):
                        pass

                    def error_handler(filename, err):
                        log('Exception in D-Bus call: %s', str(err), \
                                sender=self)

                        # Fallback: use the command line client
                        for command in util.format_desktop_command('panucci', \
                                [filename]):
                            log('Executing: %s', repr(command), sender=self)
                            subprocess.Popen(command)

                    on_error = lambda err: error_handler(filename, err)

                    # This method only exists in Panucci > 0.9 ('new Panucci')
                    i.playback_from(filename, resume_position, \
                            reply_handler=on_reply, error_handler=on_error)

                    continue # This file was handled by the D-Bus call
                except Exception, e:
                    log('Error calling Panucci using D-Bus', sender=self, traceback=True)
            elif player == 'MediaBox' and gpodder.ui.maemo:
                try:
                    MEDIABOX_NAME = 'de.pycage.mediabox'
                    MEDIABOX_PATH = '/de/pycage/mediabox/control'
                    MEDIABOX_INTF = 'de.pycage.mediabox.control'
                    o = gpodder.dbus_session_bus.get_object(MEDIABOX_NAME, MEDIABOX_PATH)
                    i = dbus.Interface(o, MEDIABOX_INTF)

                    def on_reply(*args):
                        pass

                    def on_error(err):
                        log('Exception in D-Bus call: %s', str(err), \
                                sender=self)

                    i.load(filename, '%s/x-unknown' % file_type, \
                            reply_handler=on_reply, error_handler=on_error)

                    continue # This file was handled by the D-Bus call
                except Exception, e:
                    log('Error calling MediaBox using D-Bus', sender=self, traceback=True)

            groups[player].append(filename)

        # Open episodes with system default player
        if 'default' in groups:
            # Special-casing for a single episode when the object is a PDF
            # file - this is needed on Maemo 5, so we only use gui_open()
            # for single PDF files, but still use the built-in media player
            # with an M3U file for single audio/video files. (The Maemo 5
            # media player behaves differently when opening a single-file
            # M3U playlist compared to opening the single file directly.)
            if len(groups['default']) == 1:
                fn = groups['default'][0]
                # The list of extensions is taken from gui_open in util.py
                # where all special-cases of Maemo apps are listed
                for extension in ('.pdf', '.jpg', '.jpeg', '.png'):
                    if fn.lower().endswith(extension):
                        util.gui_open(fn)
                        groups['default'] = []
                        break

            if gpodder.ui.maemo and groups['default']:
                # The Nokia Media Player app does not support receiving multiple
                # file names via D-Bus, so we simply place all file names into a
                # temporary M3U playlist and open that with the Media Player.
                m3u_filename = os.path.join(gpodder.home, 'gpodder_open_with.m3u')

                def to_url(x):
                    # Diablo's Player hates file:// URLs (Maemo bug 11647)
                    if gpodder.ui.diablo:
                        return x

                    if '://' not in x:
                        return 'file://' + urllib.quote(os.path.abspath(x))
                    return x

                util.write_m3u_playlist(m3u_filename, \
                        map(to_url, groups['default']), \
                        extm3u=False)
                util.gui_open(m3u_filename)
            else:
                for filename in groups['default']:
                    log('Opening with system default: %s', filename, sender=self)
                    util.gui_open(filename)
            del groups['default']
        elif gpodder.ui.maemo and groups:
            # When on Maemo and not opening with default, show a notification
            # (no startup notification for Panucci / MPlayer yet...)
            if len(episodes) == 1:
                text = _('Opening %s') % episodes[0].title
            else:
                count = len(episodes)
                text = N_('Opening %(count)d episode', 'Opening %(count)d episodes', count) % {'count':count}

            banner = hildon.hildon_banner_show_animation(self.gPodder, '', text)

            def destroy_banner_later(banner):
                banner.destroy()
                return False
            gobject.timeout_add(5000, destroy_banner_later, banner)

        # For each type now, go and create play commands
        for group in groups:
            for command in util.format_desktop_command(group, groups[group], resume_position):
                log('Executing: %s', repr(command), sender=self)
                subprocess.Popen(command)

        # Persist episode status changes to the database
        self.db.commit()

        # Flush updated episode status
        self.mygpo_client.flush()

    def playback_episodes(self, episodes):
        # We need to create a list, because we run through it more than once
        episodes = list(PodcastEpisode.sort_by_pubdate(e for e in episodes if \
               e.was_downloaded(and_exists=True) or self.streaming_possible()))

        try:
            self.playback_episodes_for_real(episodes)
        except Exception, e:
            log('Error in playback!', sender=self, traceback=True)
            if gpodder.ui.desktop:
                self.show_message(_('Please check your media player settings in the preferences dialog.'), \
                        _('Error opening player'), widget=self.toolPreferences)
            else:
                self.show_message(_('Please check your media player settings in the preferences dialog.'))

        channel_urls = set()
        episode_urls = set()
        for episode in episodes:
            channel_urls.add(episode.channel.url)
            episode_urls.add(episode.url)
        self.update_episode_list_icons(episode_urls)
        self.update_podcast_list_model(channel_urls)

    def play_or_download(self):
        if not gpodder.ui.fremantle:
            if self.wNotebook.get_current_page() > 0:
                if gpodder.ui.desktop:
                    self.toolCancel.set_sensitive(True)
                return

        if self.currently_updating:
            return (False, False, False, False, False, False)

        ( can_play, can_download, can_transfer, can_cancel, can_delete ) = (False,)*5
        ( is_played, is_locked ) = (False,)*2

        open_instead_of_play = False

        selection = self.treeAvailable.get_selection()
        if selection.count_selected_rows() > 0:
            (model, paths) = selection.get_selected_rows()
         
            for path in paths:
                try:
                    episode = model.get_value(model.get_iter(path), EpisodeListModel.C_EPISODE)
                except TypeError, te:
                    log('Invalid episode at path %s', str(path), sender=self)
                    continue

                if episode.file_type() not in ('audio', 'video'):
                    open_instead_of_play = True

                if episode.was_downloaded():
                    can_play = episode.was_downloaded(and_exists=True)
                    is_played = episode.is_played
                    is_locked = episode.is_locked
                    if not can_play:
                        can_download = episode.url != ''
                else:
                    if self.episode_is_downloading(episode):
                        can_cancel = True
                    else:
                        can_download = episode.url != ''

            can_download = can_download and not can_cancel
            can_play = self.streaming_possible() or (can_play and not can_cancel and not can_download)
            can_transfer = can_play and self.config.device_type != 'none' and not can_cancel and not can_download and not open_instead_of_play
            can_delete = not can_cancel

        if gpodder.ui.desktop:
            if open_instead_of_play:
                self.toolPlay.set_stock_id(gtk.STOCK_OPEN)
            else:
                self.toolPlay.set_stock_id(gtk.STOCK_MEDIA_PLAY)
            self.toolPlay.set_sensitive( can_play)
            self.toolDownload.set_sensitive( can_download)
            self.toolTransfer.set_sensitive( can_transfer)
            self.toolCancel.set_sensitive( can_cancel)

        if not gpodder.ui.fremantle:
            self.item_cancel_download.set_sensitive(can_cancel)
            self.itemDownloadSelected.set_sensitive(can_download)
            self.itemOpenSelected.set_sensitive(can_play)
            self.itemPlaySelected.set_sensitive(can_play)
            self.itemDeleteSelected.set_sensitive(can_delete)
            self.item_toggle_played.set_sensitive(can_play)
            self.item_toggle_lock.set_sensitive(can_play)
            self.itemOpenSelected.set_visible(open_instead_of_play)
            self.itemPlaySelected.set_visible(not open_instead_of_play)

        return (can_play, can_download, can_transfer, can_cancel, can_delete, open_instead_of_play)

    def on_cbMaxDownloads_toggled(self, widget, *args):
        self.spinMaxDownloads.set_sensitive(self.cbMaxDownloads.get_active())

    def on_cbLimitDownloads_toggled(self, widget, *args):
        self.spinLimitDownloads.set_sensitive(self.cbLimitDownloads.get_active())

    def episode_new_status_changed(self, urls):
        self.update_podcast_list_model()
        self.update_episode_list_icons(urls)

    def update_podcast_list_model(self, urls=None, selected=False, select_url=None):
        """Update the podcast list treeview model

        If urls is given, it should list the URLs of each
        podcast that has to be updated in the list.

        If selected is True, only update the model contents
        for the currently-selected podcast - nothing more.

        The caller can optionally specify "select_url",
        which is the URL of the podcast that is to be
        selected in the list after the update is complete.
        This only works if the podcast list has to be
        reloaded; i.e. something has been added or removed
        since the last update of the podcast list).
        """
        
        selection = self.treeChannels.get_selection()
        model, iter = selection.get_selected()

        if not self.channel_list_changed:
            # Update "all episodes" view in any case (if enabled)
            self.podcast_list_model.update_channel_proxies(self.config)

        if selected:
            print "selected"
            # very cheap! only update selected channel
            if iter is not None:
                # If we have selected the "all episodes" view, we have
                # to update all channels for selected episodes:
                if self.podcast_list_model.iter_is_proxy_row(self.config,iter):
                    urls = self.get_podcast_urls_from_selected_episodes()
                    self.podcast_list_model.update_by_urls(urls)
                else:
                    # Otherwise just update the selected row (a podcast)
                    self.podcast_list_model.update_by_filter_iter(iter)
        elif not self.channel_list_changed:
            print "not channel_list_changed"
            # we can keep the model, but have to update some
            if urls is None:
                # still cheaper than reloading the whole list
                self.podcast_list_model.update_all()
            else:
                # ok, we got a bunch of urls to update
                self.podcast_list_model.update_by_urls(urls)
        else:
            print "channel_list_changed"
            if model and iter and select_url is None:
                # Get the URL of the currently-selected podcast
                select_url = model.get_value(iter, PodcastListModel.C_URL)

            # Update the podcast list model with new channels
            self.podcast_list_model.set_channels(self.db, self.config, self.channels)

            try:
                selected_iter = model.get_iter_first()
                # Find the previously-selected URL in the new
                # model if we have an URL (else select first)
                if select_url is not None:
                    pos = model.get_iter_first()
                    while pos is not None:
                        url = model.get_value(pos, PodcastListModel.C_URL)
                        if url == select_url:
                            selected_iter = pos
                            break
                        pos = model.iter_next(pos)

                if not gpodder.ui.maemo:
                    if selected_iter is not None:
                        print "selecting"
                        selection.select_iter(selected_iter)
                    self.on_treeChannels_cursor_changed(self.treeChannels)
            except:
                log('Cannot select podcast in list', traceback=True, sender=self)
        self.channel_list_changed = False

    def episode_is_downloading(self, episode):
        """Returns True if the given episode is being downloaded at the moment"""
        if episode is None:
            return False

        return episode.url in (task.url for task in self.download_tasks_seen if task.status in (task.DOWNLOADING, task.QUEUED, task.PAUSED))

    def on_episode_list_filter_changed(self, has_episodes):
        if gpodder.ui.fremantle:
            if has_episodes:
                self.episodes_window.empty_label.hide()
                self.episodes_window.pannablearea.show()
            else:
                if self.config.episode_list_view_mode != \
                        EpisodeListModel.VIEW_ALL:
                    text = _('No episodes in current view')
                else:
                    text = _('No episodes available')
                self.episodes_window.empty_label.set_text(text)
                self.episodes_window.pannablearea.hide()
                self.episodes_window.empty_label.show()

    def update_episode_list_model(self):
        if self.channels and self.active_channel is not None:
            if gpodder.ui.fremantle:
                hildon.hildon_gtk_window_set_progress_indicator(self.episodes_window.main_window, True)

            self.currently_updating = True
            self.episode_list_model.clear()
            if gpodder.ui.fremantle:
                self.episodes_window.pannablearea.hide()
                self.episodes_window.empty_label.set_text(_('Loading episodes'))
                self.episodes_window.empty_label.show()

            def update():
                additional_args = (self.episode_is_downloading, \
                        self.config.episode_list_descriptions and gpodder.ui.desktop, \
                        self.config.episode_list_thumbnails and gpodder.ui.desktop)
                self.episode_list_model.replace_from_channel(self.active_channel, *additional_args)

                self.treeAvailable.get_selection().unselect_all()
                self.treeAvailable.scroll_to_point(0, 0)

                self.currently_updating = False
                self.play_or_download()

                if gpodder.ui.fremantle:
                    hildon.hildon_gtk_window_set_progress_indicator(\
                            self.episodes_window.main_window, False)

            util.idle_add(update)
        else:
            self.episode_list_model.clear()

    @dbus.service.method(gpodder.dbus_interface)
    def offer_new_episodes(self, channels=None):
        if gpodder.ui.fremantle:
            # Assume that when this function is called that the
            # notification is not shown anymore (Maemo bug 11345)
            self._fremantle_notification_visible = False

        new_episodes = self.get_new_episodes(channels)
        if new_episodes:
            self.new_episodes_show(new_episodes)
            return True
        return False

    def add_podcast_list(self, urls, auth_tokens=None):
        """Subscribe to a list of podcast given their URLs

        If auth_tokens is given, it should be a dictionary
        mapping URLs to (username, password) tuples."""

        if auth_tokens is None:
            auth_tokens = {}

        # Sort and split the URL list into five buckets
        queued, failed, existing, worked, authreq = [], [], [], [], []
        for input_url in urls:
            url = util.normalize_feed_url(input_url)
            if url is None:
                # Fail this one because the URL is not valid
                failed.append(input_url)
            elif self.podcast_list_model.get_filter_path_from_url(url) is not None:
                # A podcast already exists in the list for this URL
                existing.append(url)
            else:
                # This URL has survived the first round - queue for add
                queued.append(url)
                if url != input_url and input_url in auth_tokens:
                    auth_tokens[url] = auth_tokens[input_url]

        error_messages = {}
        redirections = {}

        progress = ProgressIndicator(_('Adding podcasts'), \
                _('Please wait while episode information is downloaded.'), \
                parent=self.get_dialog_parent())

        def on_after_update():
            progress.on_finished()
            # Report already-existing subscriptions to the user
            if existing:
                title = _('Existing subscriptions skipped')
                message = _('You are already subscribed to these podcasts:') \
                     + '\n\n' + '\n'.join(saxutils.escape(url) for url in existing)
                self.show_message(message, title, widget=self.treeChannels)

            # Report subscriptions that require authentication
            if authreq:
                retry_podcasts = {}
                for url in authreq:
                    title = _('Podcast requires authentication')
                    message = _('Please login to %s:') % (saxutils.escape(url),)
                    success, auth_tokens = self.show_login_dialog(title, message)
                    if success:
                        retry_podcasts[url] = auth_tokens
                    else:
                        # Stop asking the user for more login data
                        retry_podcasts = {}
                        for url in authreq:
                            error_messages[url] = _('Authentication failed')
                            failed.append(url)
                        break

                # If we have authentication data to retry, do so here
                if retry_podcasts:
                    self.add_podcast_list(retry_podcasts.keys(), retry_podcasts)

            # Report website redirections
            for url in redirections:
                title = _('Website redirection detected')
                message = _('The URL %(url)s redirects to %(target)s.') \
                        + '\n\n' + _('Do you want to visit the website now?')
                message = message % {'url': url, 'target': redirections[url]}
                if self.show_confirmation(message, title):
                    util.open_website(url)
                else:
                    break

            # Report failed subscriptions to the user
            if failed:
                title = _('Could not add some podcasts')
                message = _('Some podcasts could not be added to your list:') \
                     + '\n\n' + '\n'.join(saxutils.escape('%s: %s' % (url, \
                        error_messages.get(url, _('Unknown')))) for url in failed)
                self.show_message(message, title, important=True)

            # Upload subscription changes to gpodder.net
            self.mygpo_client.on_subscribe(worked)

            # If at least one podcast has been added, save and update all
            if self.channel_list_changed:
                # Fix URLs if mygpo has rewritten them
                self.rewrite_urls_mygpo()

                self.save_channels_opml()

                # If only one podcast was added, select it after the update
                if len(worked) == 1:
                    url = worked[0]
                else:
                    url = None

                # Update the list of subscribed podcasts
                self.update_feed_cache(force_update=False, select_url_afterwards=url)
                self.update_podcasts_tab()

                # Offer to download new episodes
                episodes = []
                for podcast in self.channels:
                    if podcast.url in worked:
                        episodes.extend(podcast.get_all_episodes())
                
                #omit episodes without downloads
                episodes = [e for e in episodes if e.url != '']
                if episodes:
                    episodes = list(PodcastEpisode.sort_by_pubdate(episodes, \
                            reverse=True))
                    self.new_episodes_show(episodes, \
                            selected=[e.check_is_new() for e in episodes])


        def thread_proc():
            # After the initial sorting and splitting, try all queued podcasts
            length = len(queued)
            for index, url in enumerate(queued):
                progress.on_progress(float(index)/float(length))
                progress.on_message(url)
                log('QUEUE RUNNER: %s', url, sender=self)
                try:
                    # The URL is valid and does not exist already - subscribe!
                    channel = PodcastChannel.load(self.db, url=url, create=True, \
                            authentication_tokens=auth_tokens.get(url, None), \
                            max_episodes=self.config.max_episodes_per_feed, \
                            download_dir=self.config.download_dir, \
                            allow_empty_feeds=self.config.allow_empty_feeds, \
                            mimetype_prefs=self.config.mimetype_prefs)

                    try:
                        username, password = util.username_password_from_url(url)
                    except ValueError, ve:
                        username, password = (None, None)

                    if username is not None and channel.username is None and \
                            password is not None and channel.password is None:
                        channel.username = username
                        channel.password = password
                        channel.save()

                    self._update_cover(channel)
                except feedcore.AuthenticationRequired:
                    if url in auth_tokens:
                        # Fail for wrong authentication data
                        error_messages[url] = _('Authentication failed')
                        failed.append(url)
                    else:
                        # Queue for login dialog later
                        authreq.append(url)
                    continue
                except feedcore.WifiLogin, error:
                    redirections[url] = error.data
                    failed.append(url)
                    error_messages[url] = _('Redirection detected')
                    continue
                except Exception, e:
                    log('Subscription error: %s', e, traceback=True, sender=self)
                    error_messages[url] = str(e)
                    failed.append(url)
                    continue

                assert channel is not None
                worked.append(channel.url)
                self.channels.append(channel)
                self.channel_list_changed = True
            util.idle_add(on_after_update)
        threading.Thread(target=thread_proc).start()

    def save_channels_opml(self):
        exporter = opml.Exporter(gpodder.subscription_file)
        return exporter.write(self.channels)

    def find_episode(self, podcast_url, episode_url):
        """Find an episode given its podcast and episode URL

        The function will return a PodcastEpisode object if
        the episode is found, or None if it's not found.
        """
        for podcast in self.channels:
            if podcast_url == podcast.url:
                for episode in podcast.get_all_episodes():
                    if episode_url == episode.url:
                        return episode

        return None

    def process_received_episode_actions(self, updated_urls):
        """Process/merge episode actions from gpodder.net

        This function will merge all changes received from
        the server to the local database and update the
        status of the affected episodes as necessary.
        """
        indicator = ProgressIndicator(_('Merging episode actions'), \
                _('Episode actions from gpodder.net are merged.'), \
                False, self.get_dialog_parent())

        for idx, action in enumerate(self.mygpo_client.get_episode_actions(updated_urls)):
            if action.action == 'play':
                episode = self.find_episode(action.podcast_url, \
                                            action.episode_url)

                if episode is not None:
                    log('Play action for %s', episode.url, sender=self)
                    episode.mark(is_played=True)

                    if action.timestamp > episode.current_position_updated and \
                            action.position is not None:
                        log('Updating position for %s', episode.url, sender=self)
                        episode.current_position = action.position
                        episode.current_position_updated = action.timestamp

                    if action.total:
                        log('Updating total time for %s', episode.url, sender=self)
                        episode.total_time = action.total

                    episode.save()
            elif action.action == 'delete':
                episode = self.find_episode(action.podcast_url, \
                                            action.episode_url)

                if episode is not None:
                    if not episode.was_downloaded(and_exists=True):
                        # Set the episode to a "deleted" state
                        log('Marking as deleted: %s', episode.url, sender=self)
                        episode.delete_from_disk()
                        episode.save()

            indicator.on_message(N_('%(count)d action processed', '%(count)d actions processed', idx) % {'count':idx})
            gtk.main_iteration(False)

        indicator.on_finished()
        self.db.commit()


    def update_feed_cache_finish_callback(self, updated_urls=None, select_url_afterwards=None):
        print("update_feed_cache_finish_callback(%s,%s)" % (updated_urls,select_url_afterwards))
        self.db.commit()
        self.updating_feed_cache = False

        self.channels = PodcastChannel.load_from_db(self.db, self.config.download_dir)

        # Process received episode actions for all updated URLs
        self.process_received_episode_actions(updated_urls)

        self.channel_list_changed = True
        self.update_podcast_list_model(select_url=select_url_afterwards)

        # Only search for new episodes in podcasts that have been
        # updated, not in other podcasts (for single-feed updates)
        episodes = self.get_new_episodes([c for c in self.channels if c.url in updated_urls])

        real_new_episode_count = len(episodes)
        print("there are %i new episodes" % real_new_episode_count)
        
        #only consider episodes with downloads
        episodes = [e for e in episodes if e.url != '']
        
        if gpodder.ui.fremantle:
            self.fancy_progress_bar.hide()
            self.button_subscribe.set_sensitive(True)
            self.button_refresh.set_sensitive(True)
            hildon.hildon_gtk_window_set_progress_indicator(self.main_window, False)
            hildon.hildon_gtk_window_set_progress_indicator(self.episodes_window.main_window, False)
            self.update_podcasts_tab()
            self.update_episode_list_model()
            if self.feed_cache_update_cancelled:
                return

            def application_in_foreground():
                try:
                    return any(w.get_property('is-topmost') for w in hildon.WindowStack.get_default().get_windows())
                except Exception, e:
                    log('Could not determine is-topmost', traceback=True)
                    # When in doubt, assume not in foreground
                    return False

            if episodes:
                if self.config.auto_download == 'quiet' and not self.config.auto_update_feeds:
                    # New episodes found, but we should do nothing
                    self.show_message(_('New episodes are available.'))
                elif self.config.auto_download == 'always' or \
                        (self.config.auto_download == 'wifi' and \
                         self.network_manager.connection_is_wlan()):
                    count = len(episodes)
                    title = N_('Downloading %(count)d new episode.', 'Downloading %(count)d new episodes.', count) % {'count':count}
                    self.show_message(title)
                    self.download_episode_list(episodes)
                elif self.config.auto_download == 'queue':
                    self.show_message(_('New episodes have been added to the download list.'))
                    self.download_episode_list_paused(episodes)
                elif application_in_foreground():
                    if not self._fremantle_notification_visible:
                        self.new_episodes_show(episodes)
                elif not self._fremantle_notification_visible:
                    try:
                        import pynotify
                        pynotify.init('gPodder')
                        n = pynotify.Notification('gPodder', _('New episodes available'), 'gpodder')
                        n.set_urgency(pynotify.URGENCY_CRITICAL)
                        n.set_hint('dbus-callback-default', ' '.join([
                            gpodder.dbus_bus_name,
                            gpodder.dbus_gui_object_path,
                            gpodder.dbus_interface,
                            'offer_new_episodes',
                        ]))
                        n.set_category('gpodder-new-episodes')
                        n.show()
                        self._fremantle_notification_visible = True
                    except Exception, e:
                        log('Error: %s', str(e), sender=self, traceback=True)
                        self.new_episodes_show(episodes)
                        self._fremantle_notification_visible = False
            elif not self.config.auto_update_feeds:
                self.show_message(_('No new episodes. Please check for new episodes later.'))
            return

        if self.tray_icon:
            self.tray_icon.set_status()

        if self.feed_cache_update_cancelled:
            # The user decided to abort the feed update
            self.show_update_feeds_buttons()
        elif not episodes:
            # Nothing new here - but inform the user
            self.pbFeedUpdate.set_fraction(1.0)
            if real_new_episode_count == 0:
                self.pbFeedUpdate.set_text(_('No new episodes'))
            else:
                message = N_('%(count)d new episode available', '%(count)d new episodes available', real_new_episode_count) % {'count':real_new_episode_count}
                self.pbFeedUpdate.set_text(message)
            self.feed_cache_update_cancelled = True
            self.btnCancelFeedUpdate.show()
            self.btnCancelFeedUpdate.set_sensitive(True)
            self.itemUpdate.set_sensitive(True)
            if gpodder.ui.maemo:
                # btnCancelFeedUpdate is a ToolButton on Maemo
                self.btnCancelFeedUpdate.set_stock_id(gtk.STOCK_APPLY)
            else:
                # btnCancelFeedUpdate is a normal gtk.Button
                self.btnCancelFeedUpdate.set_image(gtk.image_new_from_stock(gtk.STOCK_APPLY, gtk.ICON_SIZE_BUTTON))
        else:
            count = len(episodes)
            # New episodes are available
            self.pbFeedUpdate.set_fraction(1.0)
            # Are we minimized and should we auto download?
            if (self.is_iconified() and (self.config.auto_download == 'minimized')) or (self.config.auto_download == 'always'):
                self.download_episode_list(episodes)
                title = N_('Downloading %(count)d new episode.', 'Downloading %(count)d new episodes.', count) % {'count':count}
                self.show_message(title, _('New episodes available'), widget=self.labelDownloads)
                self.show_update_feeds_buttons()
            elif self.config.auto_download == 'queue':
                self.download_episode_list_paused(episodes)
                title = N_('%(count)d new episode added to download list.', '%(count)d new episodes added to download list.', count) % {'count':count}
                self.show_message(title, _('New episodes available'), widget=self.labelDownloads)
                self.show_update_feeds_buttons()
            else:
                self.show_update_feeds_buttons()
                # New episodes are available and we are not minimized
                if not self.config.do_not_show_new_episodes_dialog:
                    self.new_episodes_show(episodes, notification=True)
                else:
                    message = N_('%(count)d new episode available', '%(count)d new episodes available', count) % {'count':count}
                    self.pbFeedUpdate.set_text(message)

    def _update_cover(self, channel):
        if channel is not None and not os.path.exists(channel.cover_file) and channel.image:
            self.cover_downloader.request_cover(channel)

    def update_one_feed_cache_proc(self, queue,total):
        """worker thread for updating feeds.
            It will grab channels from the queue and
            acknowledge them using Queue.task_done()
            so that Queue.join() will succeed when the Queue
            is empty and all the feeds are refreshed.
            total is used to update the status
        """
        while not queue.empty():
            try:
                # get a channel to update
                # get() will fail after 1s if the queue is empy
                # (another thread stole the last channel since
                # empty() was called).
                # the Empty exception is caught at the end
                channel = queue.get(True,1)
                
                # when update is cancelled, the channels are still
                # dequeued to allow all threads to end
                if not self.feed_cache_update_cancelled:
                    try:
                        channel.update(max_episodes=self.config.max_episodes_per_feed, \
                                mimetype_prefs=self.config.mimetype_prefs)
                        self._update_cover(channel)
                    except Exception, e:
                        d = {'url': saxutils.escape(channel.url), 'message': saxutils.escape(str(e))}
                        if d['message']:
                            message = _('Error while updating %(url)s: %(message)s')
                        else:
                            message = _('The feed at %(url)s could not be updated.')
                        self.notification(message % d, _('Error while updating feed'), widget=self.treeChannels)
                        log('Error: %s', str(e), sender=self, traceback=True)
                
                    # By the time we get here the update may have already been cancelled
                    if not self.feed_cache_update_cancelled:
                        log("Updated %s", channel.title, sender=self)
                        
                        # must keep track somehow of the number
                        # of feeds already updated.
                        # this is stored in self.update_feed_cache_count
                        # and concurrent access is protected by
                        # self.update_feed_cache_lock
                        self.update_feed_cache_lock.acquire()
                        updated = self.update_feed_cache_count + 1
                        self.update_feed_cache_count = updated
                        self.update_feed_cache_lock.release()
                        
                        # will update the ui later on
                        self.update_feed_cache_status(updated,total,channel)
                    
                # notify main thread of completion of the task
                # (even if feed_cache_update_cancelled)
                queue.task_done()
                    
            except Queue.Empty:
                pass
            
            log("Worker thread done: %s", threading.current_thread().name)
        
        
    def update_feed_cache_proc(self, channels, select_url_afterwards):
        """update given channels in parallel.
            will exit once all channels are updated or update is cancelled
        """
        total = len(channels)
        print("update_feed_cache_proc(%i)" % total)
        
        self.update_feed_cache_lock = threading.Lock()
        self.update_feed_cache_count = 0
        
        queue = Queue.Queue(len(channels))
        
        for channel in channels:
            queue.put(channel)
        
        # 4 concurrent feed updates
        for i in range(4):
            args = (queue,total)
            t = threading.Thread(target=self.update_one_feed_cache_proc, args=args)
            t.start()
            
        # wait for all threads to be done
        queue.join()
        
        updated_urls = [c.url for c in channels]
        util.idle_add(self.update_feed_cache_finish_callback, updated_urls, select_url_afterwards)

    def update_feed_cache_status(self, updated, total, channel):
        """ display some progress information ("updated XXX (N/total)")"""
        # By the time we get here the update may have already been cancelled
        if not self.feed_cache_update_cancelled:
            def update_progress():
                d = {'podcast': channel.title, 'position': updated+1, 'total': total}
                progression = _('Updated %(podcast)s (%(position)d/%(total)d)') % d
                self.pbFeedUpdate.set_text(progression)
                if self.tray_icon:
                    self.tray_icon.set_status(self.tray_icon.STATUS_UPDATING_FEED_CACHE, progression)
                self.pbFeedUpdate.set_fraction(float(updated+1)/float(total))
            util.idle_add(update_progress)

    def show_update_feeds_buttons(self):
        # Make sure that the buttons for updating feeds
        # appear - this should happen after a feed update
        if gpodder.ui.maemo:
            self.btnUpdateSelectedFeed.show()
            self.toolFeedUpdateProgress.hide()
            self.btnCancelFeedUpdate.hide()
            self.btnCancelFeedUpdate.set_is_important(False)
            self.btnCancelFeedUpdate.set_stock_id(gtk.STOCK_CLOSE)
            self.toolbarSpacer.set_expand(True)
            self.toolbarSpacer.set_draw(False)
        else:
            self.hboxUpdateFeeds.hide()
        self.btnUpdateFeeds.show()
        self.itemUpdate.set_sensitive(True)
        self.itemUpdateChannel.set_sensitive(True)

    def on_btnCancelFeedUpdate_clicked(self, widget):
        if not self.feed_cache_update_cancelled:
            self.pbFeedUpdate.set_text(_('Cancelling...'))
            self.feed_cache_update_cancelled = True
            if not gpodder.ui.fremantle:
                self.btnCancelFeedUpdate.set_sensitive(False)
        elif not gpodder.ui.fremantle:
            self.show_update_feeds_buttons()

    def update_feed_cache(self, channels=None, force_update=True, select_url_afterwards=None):
        print("update_feed_cache(%i,%s,%s)" % (channels is not None,force_update,select_url_afterwards))
        if self.updating_feed_cache:
            if gpodder.ui.fremantle:
                self.feed_cache_update_cancelled = True
            return

        if not force_update:
            self.channels = PodcastChannel.load_from_db(self.db, self.config.download_dir)
            self.channel_list_changed = True
            self.update_podcast_list_model(select_url=select_url_afterwards)
            return

        # Fix URLs if mygpo has rewritten them
        self.rewrite_urls_mygpo()

        self.updating_feed_cache = True

        if channels is None:
            # Only update podcasts for which updates are enabled
            channels = [c for c in self.channels if c.feed_update_enabled]

        if gpodder.ui.fremantle:
            hildon.hildon_gtk_window_set_progress_indicator(self.main_window, True)
            hildon.hildon_gtk_window_set_progress_indicator(self.episodes_window.main_window, True)
            self.fancy_progress_bar.show()
            self.button_subscribe.set_sensitive(False)
            self.button_refresh.set_sensitive(False)
            self.feed_cache_update_cancelled = False
        else:
            self.itemUpdate.set_sensitive(False)
            self.itemUpdateChannel.set_sensitive(False)

            if self.tray_icon:
                self.tray_icon.set_status(self.tray_icon.STATUS_UPDATING_FEED_CACHE)

            self.feed_cache_update_cancelled = False
            self.btnCancelFeedUpdate.show()
            self.btnCancelFeedUpdate.set_sensitive(True)
            if gpodder.ui.maemo:
                self.toolbarSpacer.set_expand(False)
                self.toolbarSpacer.set_draw(True)
                self.btnUpdateSelectedFeed.hide()
                self.toolFeedUpdateProgress.show_all()
            else:
                self.btnCancelFeedUpdate.set_image(gtk.image_new_from_stock(gtk.STOCK_STOP, gtk.ICON_SIZE_BUTTON))
                self.hboxUpdateFeeds.show_all()
            self.btnUpdateFeeds.hide()

        if len(channels) == 1:
            text = _('Updating "%s"...') % channels[0].title
        else:
            count = len(channels)
            text = N_('Updating %(count)d feed...', 'Updating %(count)d feeds...', count) % {'count':count}
        self.pbFeedUpdate.set_text(text)
        self.pbFeedUpdate.set_fraction(0)

        args = (channels, select_url_afterwards)
        threading.Thread(target=self.update_feed_cache_proc, args=args).start()

    def on_gPodder_delete_event(self, widget, *args):
        """Called when the GUI wants to close the window
        Displays a confirmation dialog (and closes/hides gPodder)
        """

        downloading = self.download_status_model.are_downloads_in_progress()

        if downloading:
            if gpodder.ui.fremantle:
                self.close_gpodder()
            elif gpodder.ui.diablo:
                result = self.show_confirmation(_('Do you really want to quit gPodder now?'))
                if result:
                    self.close_gpodder()
                else:
                    return True
            dialog = gtk.MessageDialog(self.gPodder, gtk.DIALOG_MODAL, gtk.MESSAGE_QUESTION, gtk.BUTTONS_NONE)
            dialog.add_button(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL)
            quit_button = dialog.add_button(gtk.STOCK_QUIT, gtk.RESPONSE_CLOSE)

            title = _('Quit gPodder')
            message = _('You are downloading episodes. You can resume downloads the next time you start gPodder. Do you want to quit now?')

            dialog.set_title(title)
            dialog.set_markup('<span weight="bold" size="larger">%s</span>\n\n%s'%(title, message))

            quit_button.grab_focus()
            result = dialog.run()
            dialog.destroy()

            if result == gtk.RESPONSE_CLOSE:
                self.close_gpodder()
        else:
            self.close_gpodder()

        return True

    def close_gpodder(self):
        """ clean everything and exit properly
        """
        if self.channels:
            if self.save_channels_opml():
                pass # FIXME: Add mygpo synchronization here
            else:
                self.show_message(_('Please check your permissions and free disk space.'), _('Error saving podcast list'), important=True)

        self.gPodder.hide()

        if self.tray_icon is not None:
            self.tray_icon.set_visible(False)

        # Notify all tasks to to carry out any clean-up actions
        self.download_status_model.tell_all_tasks_to_quit()

        while gtk.events_pending():
            gtk.main_iteration(False)

        self.db.close()

        self.quit()
        #:sys.exit(0)

    def get_expired_episodes(self):
        for channel in self.channels:
            for episode in channel.get_downloaded_episodes():
                # Never consider locked episodes as old
                if episode.is_locked:
                    continue

                # Never consider fresh episodes as old
                if episode.age_in_days() < self.config.episode_old_age:
                    continue

                # Do not delete played episodes (except if configured)
                if episode.is_played:
                    if not self.config.auto_remove_played_episodes:
                        continue

                # Do not delete unplayed episodes (except if configured)
                if not episode.is_played:
                    if not self.config.auto_remove_unplayed_episodes:
                        continue

                yield episode

    def delete_episode_list(self, episodes, confirm=True, skip_locked=True):
        if not episodes:
            return False

        download_episodes = [e for e in episodes if e.url != '']
        
        if skip_locked:
            episodes = [e for e in episodes if not e.is_locked or e.url == '']

            if not episodes:
                title = _('Episodes are locked')
                message = _('The selected episodes are locked. Please unlock the episodes that you want to delete before trying to delete them.')
                self.notification(message, title, widget=self.treeAvailable)
                return False

        count = len(episodes)
        title = N_('Delete %(count)d episode?', 'Delete %(count)d episodes?', count) % {'count':count}
        message = _('Deleting episodes removes downloaded files.')

        if gpodder.ui.fremantle:
            message = '\n'.join([title, message])

        #ask for confirmation only if episodes contain downloaded files
        if confirm and  download_episodes:
            if not self.show_confirmation(message, title):
                return False

        progress = ProgressIndicator(_('Deleting episodes'), \
                _('Please wait while episodes are deleted'), \
                parent=self.get_dialog_parent())

        def finish_deletion(episode_urls, channel_urls):
            progress.on_finished()

            # Episodes have been deleted - persist the database
            self.db.commit()

            self.update_episode_list_icons(episode_urls)
            self.update_podcast_list_model(channel_urls)
            self.play_or_download()

        def thread_proc():
            episode_urls = set()
            channel_urls = set()

            episodes_status_update = []
            for idx, episode in enumerate(episodes):
                progress.on_progress(float(idx)/float(len(episodes)))
                if episode.is_locked and episode.url != '' and skip_locked:
                    log('Not deleting episode (is locked): %s', episode.title)
                else:
                    log('Deleting episode: %s', episode.title)
                    progress.on_message(episode.title)
                    episode.delete_from_disk()
                    episode_urls.add(episode.url)
                    channel_urls.add(episode.channel.url)
                    episodes_status_update.append(episode)

                    # Tell the shownotes window that we have removed the episode
                    if self.episode_shownotes_window is not None and \
                            self.episode_shownotes_window.episode is not None and \
                            self.episode_shownotes_window.episode.url == episode.url:
                        util.idle_add(self.episode_shownotes_window._download_status_changed, None)

            # Notify the web service about the status update + upload
            self.mygpo_client.on_delete(episodes_status_update)
            self.mygpo_client.flush()

            util.idle_add(finish_deletion, episode_urls, channel_urls)

        threading.Thread(target=thread_proc).start()

        return True

    def on_itemRemoveOldEpisodes_activate(self, widget):
        self.show_delete_episodes_window()

    def show_delete_episodes_window(self, channel=None):
        """Offer deletion of episodes

        If channel is None, offer deletion of all episodes.
        Otherwise only offer deletion of episodes in the channel.
        """
        if gpodder.ui.maemo:
            columns = (
                ('maemo_remove_markup', None, None, _('Episode')),
            )
        else:
            columns = (
                ('title_markup', None, None, _('Episode')),
                ('filesize_prop', 'length', gobject.TYPE_INT, _('Size')),
                ('pubdate_prop', 'pubDate', gobject.TYPE_INT, _('Released')),
                ('played_prop', None, None, _('Status')),
                ('age_prop', 'age_int_prop', gobject.TYPE_INT, _('Downloaded')),
            )

        msg_older_than = N_('Select older than %(count)d day', 'Select older than %(count)d days', self.config.episode_old_age)
        selection_buttons = {
                _('Select played'): lambda episode: episode.is_played,
                _('Select finished'): lambda episode: episode.is_finished(),
                msg_older_than % {'count':self.config.episode_old_age}: lambda episode: episode.age_in_days() > self.config.episode_old_age,
        }

        instructions = _('Select the episodes you want to delete:')

        if channel is None:
            channels = self.channels
        else:
            channels = [channel]

        episodes = []
        for channel in channels:
            for episode in channel.get_downloaded_episodes():
                # Disallow deletion of locked episodes that still exist
                if not episode.is_locked or not episode.file_exists():
                    episodes.append(episode)

        selected = [e.is_played or not e.file_exists() for e in episodes]

        gPodderEpisodeSelector(self.gPodder, title = _('Delete episodes'), instructions = instructions, \
                                episodes = episodes, selected = selected, columns = columns, \
                                stock_ok_button = gtk.STOCK_DELETE, callback = self.delete_episode_list, \
                                selection_buttons = selection_buttons, _config=self.config, \
                                show_episode_shownotes=self.show_episode_shownotes)

    def on_selected_episodes_status_changed(self):
        # The order of the updates here is important! When "All episodes" is
        # selected, the update of the podcast list model depends on the episode
        # list selection to determine which podcasts are affected. Updating
        # the episode list could remove the selection if a filter is active.
        self.update_podcast_list_model(selected=True)
        self.update_episode_list_icons(selected=True)
        self.db.commit()

    def mark_selected_episodes_new(self):
        for episode in self.get_selected_episodes():
            episode.mark_new()
        self.on_selected_episodes_status_changed()

    def mark_selected_episodes_old(self):
        for episode in self.get_selected_episodes():
            episode.mark_old()
        self.on_selected_episodes_status_changed()

    def on_item_toggle_played_activate( self, widget, toggle = True, new_value = False):
        for episode in self.get_selected_episodes():
            if toggle:
                episode.mark(is_played=not episode.is_played)
            else:
                episode.mark(is_played=new_value)
        self.on_selected_episodes_status_changed()

    def on_item_toggle_lock_activate(self, widget, toggle=True, new_value=False):
        for episode in self.get_selected_episodes():
            if toggle:
                episode.mark(is_locked=not episode.is_locked)
            else:
                episode.mark(is_locked=new_value)
        self.on_selected_episodes_status_changed()

    def on_channel_toggle_lock_activate(self, widget, toggle=True, new_value=False):
        if self.active_channel is None:
            return

        self.active_channel.channel_is_locked = not self.active_channel.channel_is_locked
        self.active_channel.update_channel_lock()

        for episode in self.active_channel.get_all_episodes():
            episode.mark(is_locked=self.active_channel.channel_is_locked)

        self.update_podcast_list_model(selected=True)
        self.update_episode_list_icons(all=True)

    def on_itemUpdateChannel_activate(self, widget=None):
        if self.active_channel is None:
            title = _('No podcast selected')
            message = _('Please select a podcast in the podcasts list to update.')
            self.show_message( message, title, widget=self.treeChannels)
            return

        # Dirty hack to check for "All episodes" (see gpodder.gtkui.model)
        if getattr(self.active_channel, 'ALL_EPISODES_PROXY', False):
            self.update_feed_cache()
        else:
            self.update_feed_cache(channels=[self.active_channel])

    def on_itemUpdate_activate(self, widget=None):
        # Check if we have outstanding subscribe/unsubscribe actions
        if self.on_add_remove_podcasts_mygpo():
            log('Update cancelled (received server changes)', sender=self)
            return

        if self.channels:
            self.update_feed_cache()
        else:
            welcome_window = gPodderWelcome(self.main_window,
                    center_on_widget=self.main_window,
                    show_example_podcasts_callback=self.on_itemImportChannels_activate,
                    setup_my_gpodder_callback=self.on_download_subscriptions_from_mygpo)

            result = welcome_window.main_window.run()

            welcome_window.main_window.destroy()
            if result == gPodderWelcome.RESPONSE_OPML:
                self.on_itemImportChannels_activate(None)
            elif result == gPodderWelcome.RESPONSE_MYGPO:
                self.on_download_subscriptions_from_mygpo(None)

    def download_episode_list_paused(self, episodes):
        self.download_episode_list(episodes, True)

    def download_episode_list(self, episodes, add_paused=False, force_start=False):
        enable_update = False

        for episode in episodes:
            log('Downloading episode: %s', episode.title, sender = self)
            if not episode.was_downloaded(and_exists=True):
                task_exists = False
                for task in self.download_tasks_seen:
                    if episode.url == task.url and task.status not in (task.DOWNLOADING, task.QUEUED):
                        self.download_queue_manager.add_task(task, force_start)
                        enable_update = True
                        task_exists = True
                        continue

                if task_exists:
                    continue

                try:
                    task = download.DownloadTask(episode, self.config)
                except Exception, e:
                    d = {'episode': episode.title, 'message': str(e)}
                    message = _('Download error while downloading %(episode)s: %(message)s')
                    self.show_message(message % d, _('Download error'), important=True)
                    log('Download error while downloading %s', episode.title, sender=self, traceback=True)
                    continue

                if add_paused:
                    task.status = task.PAUSED
                else:
                    self.mygpo_client.on_download([task.episode])
                    self.download_queue_manager.add_task(task, force_start)

                self.download_status_model.register_task(task)
                enable_update = True

        if enable_update:
            self.enable_download_list_update()

        # Flush updated episode status
        self.mygpo_client.flush()

    def cancel_task_list(self, tasks):
        if not tasks:
            return

        for task in tasks:
            if task.status in (task.QUEUED, task.DOWNLOADING):
                task.status = task.CANCELLED
            elif task.status == task.PAUSED:
                task.status = task.CANCELLED
                # Call run, so the partial file gets deleted
                task.run()

        self.update_episode_list_icons([task.url for task in tasks])
        self.play_or_download()

        # Update the tab title and downloads list
        self.update_downloads_list()

    def new_episodes_show(self, episodes, notification=False, selected=None):
        if gpodder.ui.maemo:
            columns = (
                ('maemo_markup', None, None, _('Episode')),
            )
            show_notification = notification
        else:
            columns = (
                ('title_markup', None, None, _('Episode')),
                ('filesize_prop', 'length', gobject.TYPE_INT, _('Size')),
                ('pubdate_prop', 'pubDate', gobject.TYPE_INT, _('Released')),
            )
            show_notification = False

        instructions = _('Select the episodes you want to download:')

        if self.new_episodes_window is not None:
            self.new_episodes_window.main_window.destroy()
            self.new_episodes_window = None

        def download_episodes_callback(episodes):
            self.new_episodes_window = None
            self.download_episode_list(episodes)

        if selected is None:
            # Select all by default
            selected = [True]*len(episodes)

        self.new_episodes_window = gPodderEpisodeSelector(self.gPodder, \
                title=_('New episodes available'), \
                instructions=instructions, \
                episodes=episodes, \
                columns=columns, \
                selected=selected, \
                stock_ok_button = 'gpodder-download', \
                callback=download_episodes_callback, \
                remove_callback=lambda e: e.mark_old(), \
                remove_action=_('Mark as old'), \
                remove_finished=self.episode_new_status_changed, \
                _config=self.config, \
                show_notification=show_notification, \
                show_episode_shownotes=self.show_episode_shownotes)
        pass

    def on_itemDownloadAllNew_activate(self, widget, *args):
        if not self.offer_new_episodes():
            self.show_message(_('Please check for new episodes later.'), \
                    _('No new episodes available'), widget=self.btnUpdateFeeds)

    def get_new_episodes(self, channels=None):
        if channels is None:
            channels = self.channels
        episodes = []
        for channel in channels:
            for episode in channel.get_new_episodes(downloading=self.episode_is_downloading):
                episodes.append(episode)

        return episodes

    @dbus.service.method(gpodder.dbus_interface)
    def start_device_synchronization(self):
        """Public D-Bus API for starting Device sync (Desktop only)

        This method can be called to initiate a synchronization with
        a configured protable media player. This only works for the
        Desktop version of gPodder and does nothing on Maemo.
        """
        if gpodder.ui.desktop:
            self.on_sync_to_ipod_activate(None)
            return True

        return False

    def on_sync_to_ipod_activate(self, widget, episodes=None, force_played=True):
        self.sync_ui.on_synchronize_episodes(self.channels, episodes, force_played)

    def commit_changes_to_database(self):
        """This will be called after the sync process is finished"""
        self.db.commit()

    def on_cleanup_ipod_activate(self, widget, *args):
        self.sync_ui.on_cleanup_device()

    def on_manage_device_playlist(self, widget):
        self.sync_ui.on_manage_device_playlist()

    def show_hide_tray_icon(self):
        if self.config.display_tray_icon and have_trayicon and self.tray_icon is None:
            self.tray_icon = GPodderStatusIcon(self, gpodder.icon_file, self.config)
        elif not self.config.display_tray_icon and self.tray_icon:
            self.tray_icon.set_visible(False)
            del self.tray_icon
            self.tray_icon = None

        if self.tray_icon:
            self.tray_icon.set_visible(True)

    def on_itemShowAllEpisodes_activate(self, widget):
        self.config.podcast_list_view_all = widget.get_active()

    def on_itemShowNewEpisodes_activate(self, widget):
        self.config.podcast_list_view_new = widget.get_active()

    def on_itemShowToolbar_activate(self, widget):
        self.config.show_toolbar = self.itemShowToolbar.get_active()

    def on_itemShowDescription_activate(self, widget):
        self.config.episode_list_descriptions = self.itemShowDescription.get_active()

    def on_item_view_hide_boring_podcasts_toggled(self, toggleaction):
        self.config.podcast_list_hide_boring = toggleaction.get_active()
        if self.config.podcast_list_hide_boring:
            self.podcast_list_model.set_view_mode(self.config.episode_list_view_mode)
        else:
            self.podcast_list_model.set_view_mode(-1)

    def on_item_view_podcasts_changed(self, radioaction, current):
        # Only on Fremantle
        if current == self.item_view_podcasts_all:
            self.podcast_list_model.set_view_mode(-1)
        elif current == self.item_view_podcasts_downloaded:
            self.podcast_list_model.set_view_mode(EpisodeListModel.VIEW_DOWNLOADED)
        elif current == self.item_view_podcasts_unplayed:
            self.podcast_list_model.set_view_mode(EpisodeListModel.VIEW_UNPLAYED)

        self.config.podcast_list_view_mode = self.podcast_list_model.get_view_mode()

    def on_item_view_episodes_changed(self, radioaction, current):
        if current == self.item_view_episodes_all:
            self.config.episode_list_view_mode = EpisodeListModel.VIEW_ALL
        elif current == self.item_view_episodes_undeleted:
            self.config.episode_list_view_mode = EpisodeListModel.VIEW_UNDELETED
        elif current == self.item_view_episodes_downloaded:
            self.config.episode_list_view_mode = EpisodeListModel.VIEW_DOWNLOADED
        elif current == self.item_view_episodes_unplayed:
            self.config.episode_list_view_mode = EpisodeListModel.VIEW_UNPLAYED

        self.episode_list_model.set_view_mode(self.config.episode_list_view_mode)

        if self.config.podcast_list_hide_boring and not gpodder.ui.fremantle:
            self.podcast_list_model.set_view_mode(self.config.episode_list_view_mode)

    def update_item_device( self):
        if not gpodder.ui.fremantle:
            if self.config.device_type != 'none':
                self.itemDevice.set_visible(True)
                self.itemDevice.label = self.get_device_name()
            else:
                self.itemDevice.set_visible(False)

    def properties_closed( self):
        self.preferences_dialog = None
        self.show_hide_tray_icon()
        self.update_item_device()
        if gpodder.ui.maemo:
            selection = self.treeAvailable.get_selection()
            if self.config.maemo_enable_gestures or \
                    self.config.enable_fingerscroll:
                selection.set_mode(gtk.SELECTION_SINGLE)
            else:
                selection.set_mode(gtk.SELECTION_MULTIPLE)

    def on_itemPreferences_activate(self, widget, *args):
        self.preferences_dialog = gPodderPreferences(self.main_window, \
                _config=self.config, \
                callback_finished=self.properties_closed, \
                user_apps_reader=self.user_apps_reader, \
                parent_window=self.main_window, \
                mygpo_client=self.mygpo_client, \
                on_send_full_subscriptions=self.on_send_full_subscriptions, \
                on_itemExportChannels_activate=self.on_itemExportChannels_activate)

        # Initial message to relayout window (in case it's opened in portrait mode
        self.preferences_dialog.on_window_orientation_changed(self._last_orientation)

    def on_itemDependencies_activate(self, widget):
        gPodderDependencyManager(self.gPodder)

    def on_goto_mygpo(self, widget):
        self.mygpo_client.open_website()

    def on_download_subscriptions_from_mygpo(self, action=None):
        title = _('Login to gpodder.net')
        message = _('Please login to download your subscriptions.')
        success, (username, password) = self.show_login_dialog(title, message, \
                self.config.mygpo_username, self.config.mygpo_password)
        if not success:
            return

        self.config.mygpo_username = username
        self.config.mygpo_password = password

        dir = gPodderPodcastDirectory(self.gPodder, _config=self.config, \
                custom_title=_('Subscriptions on gpodder.net'), \
                add_urls_callback=self.add_podcast_list, \
                hide_url_entry=True)

        # TODO: Refactor this into "gpodder.my" or mygpoclient, so that
        #       we do not have to hardcode the URL here
        OPML_URL = 'http://gpodder.net/subscriptions/%s.opml' % self.config.mygpo_username
        url = util.url_add_authentication(OPML_URL, \
                self.config.mygpo_username, \
                self.config.mygpo_password)
        dir.download_opml_file(url)

    def on_mygpo_settings_activate(self, action=None):
        # This dialog is only used for Maemo 4
        if not gpodder.ui.diablo:
            return

        settings = MygPodderSettings(self.main_window, \
                config=self.config, \
                mygpo_client=self.mygpo_client, \
                on_send_full_subscriptions=self.on_send_full_subscriptions)

    def on_itemAddChannel_activate(self, widget=None):
        gPodderAddPodcast(self.gPodder, \
                add_urls_callback=self.add_podcast_list)

    def on_itemEditChannel_activate(self, widget, *args):
        if self.active_channel is None:
            title = _('No podcast selected')
            message = _('Please select a podcast in the podcasts list to edit.')
            self.show_message( message, title, widget=self.treeChannels)
            return

        callback_closed = lambda: self.update_podcast_list_model(selected=True)
        gPodderChannel(self.main_window, \
                channel=self.active_channel, \
                callback_closed=callback_closed, \
                cover_downloader=self.cover_downloader)

    def on_itemMassUnsubscribe_activate(self, item=None):
        columns = (
            ('title', None, None, _('Podcast')),
        )

        # We're abusing the Episode Selector for selecting Podcasts here,
        # but it works and looks good, so why not? -- thp
        gPodderEpisodeSelector(self.main_window, \
                title=_('Remove podcasts'), \
                instructions=_('Select the podcast you want to remove.'), \
                episodes=self.channels, \
                columns=columns, \
                size_attribute=None, \
                stock_ok_button=_('Remove'), \
                callback=self.remove_podcast_list, \
                _config=self.config)

    def remove_podcast_list(self, channels, confirm=True):
        if not channels:
            log('No podcasts selected for deletion', sender=self)
            return

        if len(channels) == 1:
            title = _('Removing podcast')
            info = _('Please wait while the podcast is removed')
            message = _('Do you really want to remove this podcast and its episodes?')
        else:
            title = _('Removing podcasts')
            info = _('Please wait while the podcasts are removed')
            message = _('Do you really want to remove the selected podcasts and their episodes?')

        if confirm and not self.show_confirmation(message, title):
            return

        progress = ProgressIndicator(title, info, parent=self.get_dialog_parent())

        def finish_deletion(select_url):
            # Upload subscription list changes to the web service
            self.mygpo_client.on_unsubscribe([c.url for c in channels])

            # Re-load the channels and select the desired new channel
            self.update_feed_cache(force_update=False, select_url_afterwards=select_url)
            progress.on_finished()
            self.update_podcasts_tab()

        def thread_proc():
            select_url = None

            for idx, channel in enumerate(channels):
                # Update the UI for correct status messages
                progress.on_progress(float(idx)/float(len(channels)))
                progress.on_message(channel.title)

                # Delete downloaded episodes
                channel.remove_downloaded()

                # cancel any active downloads from this channel
                for episode in channel.get_all_episodes():
                    util.idle_add(self.download_status_model.cancel_by_url,
                            episode.url)

                if len(channels) == 1:
                    # get the URL of the podcast we want to select next
                    if channel in self.channels:
                        position = self.channels.index(channel)
                    else:
                        position = -1

                    if position == len(self.channels)-1:
                        # this is the last podcast, so select the URL
                        # of the item before this one (i.e. the "new last")
                        select_url = self.channels[position-1].url
                    else:
                        # there is a podcast after the deleted one, so
                        # we simply select the one that comes after it
                        select_url = self.channels[position+1].url

                # Remove the channel and clean the database entries
                channel.delete()
                self.channels.remove(channel)

            # Clean up downloads and download directories
            self.clean_up_downloads()

            self.channel_list_changed = True
            self.save_channels_opml()

            # The remaining stuff is to be done in the GTK main thread
            util.idle_add(finish_deletion, select_url)

        threading.Thread(target=thread_proc).start()

    def on_itemRemoveChannel_activate(self, widget, *args):
        if self.active_channel is None:
            title = _('No podcast selected')
            message = _('Please select a podcast in the podcasts list to remove.')
            self.show_message( message, title, widget=self.treeChannels)
            return

        self.remove_podcast_list([self.active_channel])

    def get_opml_filter(self):
        filter = gtk.FileFilter()
        filter.add_pattern('*.opml')
        filter.add_pattern('*.xml')
        filter.set_name(_('OPML files')+' (*.opml, *.xml)')
        return filter

    def on_item_import_from_file_activate(self, widget, filename=None):
        if filename is None:
            if gpodder.ui.desktop or gpodder.ui.fremantle:
                dlg = gtk.FileChooserDialog(title=_('Import from OPML'), \
                        parent=None, action=gtk.FILE_CHOOSER_ACTION_OPEN)
                dlg.add_button(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL)
                dlg.add_button(gtk.STOCK_OPEN, gtk.RESPONSE_OK)
            elif gpodder.ui.diablo:
                dlg = hildon.FileChooserDialog(self.gPodder, gtk.FILE_CHOOSER_ACTION_OPEN)
            dlg.set_filter(self.get_opml_filter())
            response = dlg.run()
            filename = None
            if response == gtk.RESPONSE_OK:
                filename = dlg.get_filename()
            dlg.destroy()

        if filename is not None:
            dir = gPodderPodcastDirectory(self.gPodder, _config=self.config, \
                    custom_title=_('Import podcasts from OPML file'), \
                    add_urls_callback=self.add_podcast_list, \
                    hide_url_entry=True)
            dir.download_opml_file(filename)

    def on_itemExportChannels_activate(self, widget, *args):
        if not self.channels:
            title = _('Nothing to export')
            message = _('Your list of podcast subscriptions is empty. Please subscribe to some podcasts first before trying to export your subscription list.')
            self.show_message(message, title, widget=self.treeChannels)
            return

        if gpodder.ui.desktop:
            dlg = gtk.FileChooserDialog(title=_('Export to OPML'), parent=self.gPodder, action=gtk.FILE_CHOOSER_ACTION_SAVE)
            dlg.add_button(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL)
            dlg.add_button(gtk.STOCK_SAVE, gtk.RESPONSE_OK)
        elif gpodder.ui.fremantle:
            dlg = gobject.new(hildon.FileChooserDialog, \
                    action=gtk.FILE_CHOOSER_ACTION_SAVE)
            dlg.set_title(_('Export to OPML'))
        elif gpodder.ui.diablo:
            dlg = hildon.FileChooserDialog(self.gPodder, gtk.FILE_CHOOSER_ACTION_SAVE)
        dlg.set_filter(self.get_opml_filter())
        response = dlg.run()
        if response == gtk.RESPONSE_OK:
            filename = dlg.get_filename()
            dlg.destroy()
            exporter = opml.Exporter( filename)
            if filename is not None and exporter.write(self.channels):
                count = len(self.channels)
                title = N_('%(count)d subscription exported', '%(count)d subscriptions exported', count) % {'count':count}
                self.show_message(_('Your podcast list has been successfully exported.'), title, widget=self.treeChannels)
            else:
                self.show_message( _('Could not export OPML to file. Please check your permissions.'), _('OPML export failed'), important=True)
        else:
            dlg.destroy()

    def on_itemImportChannels_activate(self, widget, *args):
        if gpodder.ui.fremantle:
            gPodderPodcastDirectory.show_add_podcast_picker(self.main_window, \
                    self.config.toplist_url, \
                    self.config.opml_url, \
                    self.add_podcast_list, \
                    self.on_itemAddChannel_activate, \
                    self.on_download_subscriptions_from_mygpo, \
                    self.show_text_edit_dialog)
        else:
            dir = gPodderPodcastDirectory(self.main_window, _config=self.config, \
                    add_urls_callback=self.add_podcast_list)
            util.idle_add(dir.download_opml_file, self.config.opml_url)

    def on_homepage_activate(self, widget, *args):
        util.open_website(gpodder.__url__)

    def on_wiki_activate(self, widget, *args):
        util.open_website('http://gpodder.org/wiki/User_Manual')

    def on_bug_tracker_activate(self, widget, *args):
        if gpodder.ui.maemo:
            util.open_website('http://bugs.maemo.org/enter_bug.cgi?product=gPodder')
        else:
            util.open_website('https://bugs.gpodder.org/enter_bug.cgi?product=gPodder')

    def on_item_support_activate(self, widget):
        util.open_website('http://gpodder.org/donate')

    def on_itemAbout_activate(self, widget, *args):
        if gpodder.ui.fremantle:
            from gpodder.gtkui.frmntl.about import HeAboutDialog
            HeAboutDialog.present(self.main_window,
                                 'gPodder',
                                 'gpodder',
                                 gpodder.__version__,
                                 _('A podcast client with focus on usability'),
                                 gpodder.__copyright__,
                                 gpodder.__url__,
                                 'http://bugs.maemo.org/enter_bug.cgi?product=gPodder',
                                 'http://gpodder.org/donate')
            return

        dlg = gtk.AboutDialog()
        dlg.set_transient_for(self.main_window)
        dlg.set_name('gPodder')
        dlg.set_version(gpodder.__version__)
        dlg.set_copyright(gpodder.__copyright__)
        dlg.set_comments(_('A podcast client with focus on usability'))
        dlg.set_website(gpodder.__url__)
        dlg.set_translator_credits( _('translator-credits'))
        dlg.connect( 'response', lambda dlg, response: dlg.destroy())

        if gpodder.ui.desktop:
            # For the "GUI" version, we add some more
            # items to the about dialog (credits and logo)
            app_authors = [
                    _('Maintainer:'),
                    'Thomas Perl <thp.io>',
            ]

            if os.path.exists(gpodder.credits_file):
                credits = open(gpodder.credits_file).read().strip().split('\n')
                app_authors += ['', _('Patches, bug reports and donations by:')]
                app_authors += credits

            dlg.set_authors(app_authors)
            try:
                dlg.set_logo(gtk.gdk.pixbuf_new_from_file(gpodder.icon_file))
            except:
                dlg.set_logo_icon_name('gpodder')

        dlg.run()

    def on_wNotebook_switch_page(self, widget, *args):
        page_num = args[1]
        if gpodder.ui.maemo:
            self.tool_downloads.set_active(page_num == 1)
            page = self.wNotebook.get_nth_page(page_num)
            tab_label = self.wNotebook.get_tab_label(page).get_text()
            if page_num == 0 and self.active_channel is not None:
                self.set_title(self.active_channel.title)
            else:
                self.set_title(tab_label)
        if page_num == 0:
            self.play_or_download()
            self.menuChannels.set_sensitive(True)
            self.menuSubscriptions.set_sensitive(True)
            # The message area in the downloads tab should be hidden
            # when the user switches away from the downloads tab
            if self.message_area is not None:
                self.message_area.hide()
                self.message_area = None
        else:
            self.menuChannels.set_sensitive(False)
            self.menuSubscriptions.set_sensitive(False)
            if gpodder.ui.desktop:
                self.toolDownload.set_sensitive(False)
                self.toolPlay.set_sensitive(False)
                self.toolTransfer.set_sensitive(False)
                self.toolCancel.set_sensitive(False)

    def on_treeChannels_row_activated(self, widget, path, *args):
        # double-click action of the podcast list or enter
        self.treeChannels.set_cursor(path)

    def on_treeChannels_cursor_changed(self, widget, *args):
        ( model, iter ) = self.treeChannels.get_selection().get_selected()

        if model is not None and iter is not None:
            old_active_channel = self.active_channel
            self.active_channel = model.get_value(iter, PodcastListModel.C_CHANNEL)

            if self.active_channel == old_active_channel:
                return

            if gpodder.ui.maemo:
                self.set_title(self.active_channel.title)

            # Dirty hack to check for "All episodes" (see gpodder.gtkui.model)
            if getattr(self.active_channel, 'ALL_EPISODES_PROXY', False):
                self.itemEditChannel.set_visible(False)
                self.itemRemoveChannel.set_visible(False)
            else:
                self.itemEditChannel.set_visible(True)
                self.itemRemoveChannel.set_visible(True)
        else:
            self.active_channel = None
            self.itemEditChannel.set_visible(False)
            self.itemRemoveChannel.set_visible(False)

        self.update_episode_list_model()

    def on_btnEditChannel_clicked(self, widget, *args):
        self.on_itemEditChannel_activate( widget, args)

    def get_podcast_urls_from_selected_episodes(self):
        """Get a set of podcast URLs based on the selected episodes"""
        return set(episode.channel.url for episode in \
                self.get_selected_episodes())

    def get_selected_episodes(self):
        """Get a list of selected episodes from treeAvailable"""
        selection = self.treeAvailable.get_selection()
        model, paths = selection.get_selected_rows()

        episodes = [model.get_value(model.get_iter(path), EpisodeListModel.C_EPISODE) for path in paths]
        return episodes

    def on_transfer_selected_episodes(self, widget):
        self.on_sync_to_ipod_activate(widget, self.get_selected_episodes())

    def on_playback_selected_episodes(self, widget):
        self.playback_episodes(self.get_selected_episodes())

    def on_shownotes_selected_episodes(self, widget):
        episodes = self.get_selected_episodes()
        if episodes:
            episode = episodes.pop(0)
            self.show_episode_shownotes(episode)
        else:
            self.show_message(_('Please select an episode from the episode list to display shownotes.'), _('No episode selected'), widget=self.treeAvailable)

    def on_download_selected_episodes(self, widget):
        episodes = self.get_selected_episodes()
        self.download_episode_list(episodes)
        self.update_episode_list_icons([episode.url for episode in episodes])
        self.play_or_download()

    def on_treeAvailable_selection_changed(self, widget):
        """selection changed handler for treeAvailable"""
        # Only display the first episode
        if widget.count_selected_rows() == 0:
            self.clear_embedded_notes()
        else:
            e = self.get_selected_episodes()[0]
            self.display_embedded_notes(e)

    def on_treeAvailable_row_activated(self, widget, path, view_column):
        """Double-click/enter action handler for treeAvailable"""
        # We should only have one one selected as it was double clicked!
        e = self.get_selected_episodes()[0]
        
        if (self.config.double_click_episode_action == 'download'):
            # If the episode has already been downloaded and exists then play it
            if e.was_downloaded(and_exists=True):
                self.playback_episodes(self.get_selected_episodes())
            # else download it if it is not already downloading
            elif not self.episode_is_downloading(e): 
                self.download_episode_list([e])
                self.update_episode_list_icons([e.url])
                self.play_or_download()
        elif (self.config.double_click_episode_action == 'stream'):
            # If we happen to have downloaded this episode simple play it
            if e.was_downloaded(and_exists=True):
                self.playback_episodes(self.get_selected_episodes())
            # else if streaming is possible stream it    
            elif self.streaming_possible():
                self.playback_episodes(self.get_selected_episodes())
            else:
                log('Unable to stream episode - default media player selected!', sender=self, traceback=True)
                self.show_message(_('Please check your media player settings in the preferences dialog.'), _('Unable to stream episode'), widget=self.toolPreferences)
        else:
            # default action is to display show notes
            self.on_shownotes_selected_episodes(widget)

    def show_episode_shownotes(self, episode):
        if self.episode_shownotes_window is None:
            log('First-time use of episode window --- creating', sender=self)
            self.episode_shownotes_window = gPodderShownotes(self.gPodder, _config=self.config, \
                    _download_episode_list=self.download_episode_list, \
                    _playback_episodes=self.playback_episodes, \
                    _delete_episode_list=self.delete_episode_list, \
                    _episode_list_status_changed=self.episode_list_status_changed, \
                    _cancel_task_list=self.cancel_task_list, \
                    _episode_is_downloading=self.episode_is_downloading, \
                    _streaming_possible=self.streaming_possible())
        self.episode_shownotes_window.show(episode)
        if self.episode_is_downloading(episode):
            self.update_downloads_list()

    def restart_auto_update_timer(self):
        if self._auto_update_timer_source_id is not None:
            log('Removing existing auto update timer.', sender=self)
            gobject.source_remove(self._auto_update_timer_source_id)
            self._auto_update_timer_source_id = None

        if self.config.auto_update_feeds and \
                self.config.auto_update_frequency:
            interval = 60*1000*self.config.auto_update_frequency
            log('Setting up auto update timer with interval %d.', \
                    self.config.auto_update_frequency, sender=self)
            self._auto_update_timer_source_id = gobject.timeout_add(\
                    interval, self._on_auto_update_timer)

    def _on_auto_update_timer(self):
        log('Auto update timer fired.', sender=self)
        self.update_feed_cache(force_update=True)

        # Ask web service for sub changes (if enabled)
        self.mygpo_client.flush()

        return True


    def restart_read_timer(self,episode):
        if self._read_timer_source_id is not None:
            log('Removing existing read timer.', sender=self)
            gobject.source_remove(self._read_timer_source_id)
            self._read_timer_source_id = None
        
        # was called only to clear timeout
        if episode is None:
            return
        
        if episode.url == '':
            if episode.state == gpodder.STATE_NORMAL\
                    and not episode.is_played:
                print("new episode without enclosure, prepare mark as old")

                def mark_old():
                    print("marking %s as old" % episode.title)
                    episode.mark_old()
                    self.on_selected_episodes_status_changed()
                    return False
                
                interval = 1000*2
                log('Setting up mark read timer with interval %ds.', \
                        interval, sender=self)
                self._read_timer_source_id = gobject.timeout_add(\
                        interval, mark_old)
        else:
            print("episode %s with enclosure %s" % (episode.title,episode.url))

    def on_treeDownloads_row_activated(self, widget, *args):
        # Use the standard way of working on the treeview
        selection = self.treeDownloads.get_selection()
        (model, paths) = selection.get_selected_rows()
        selected_tasks = [(gtk.TreeRowReference(model, path), model.get_value(model.get_iter(path), 0)) for path in paths]

        for tree_row_reference, task in selected_tasks:
            if task.status in (task.DOWNLOADING, task.QUEUED):
                task.status = task.PAUSED
            elif task.status in (task.CANCELLED, task.PAUSED, task.FAILED):
                self.download_queue_manager.add_task(task)
                self.enable_download_list_update()
            elif task.status == task.DONE:
                model.remove(model.get_iter(tree_row_reference.get_path()))
                
        self.play_or_download()

        # Update the tab title and downloads list
        self.update_downloads_list()

    def on_item_cancel_download_activate(self, widget):
        if self.wNotebook.get_current_page() == 0:
            selection = self.treeAvailable.get_selection()
            (model, paths) = selection.get_selected_rows()
            urls = [model.get_value(model.get_iter(path), \
                    self.episode_list_model.C_URL) for path in paths]
            selected_tasks = [task for task in self.download_tasks_seen \
                    if task.url in urls]
        else:
            selection = self.treeDownloads.get_selection()
            (model, paths) = selection.get_selected_rows()
            selected_tasks = [model.get_value(model.get_iter(path), \
                    self.download_status_model.C_TASK) for path in paths]
        self.cancel_task_list(selected_tasks)

    def on_btnCancelAll_clicked(self, widget, *args):
        self.cancel_task_list(self.download_tasks_seen)

    def on_btnDownloadedDelete_clicked(self, widget, *args):
        episodes = self.get_selected_episodes()
        if len(episodes) == 1:
            self.delete_episode_list(episodes, skip_locked=False)
        else:
            self.delete_episode_list(episodes)

    def on_key_press(self, widget, event):
        # Allow tab switching with Ctrl + PgUp/PgDown
        if event.state & gtk.gdk.CONTROL_MASK:
            if event.keyval == gtk.keysyms.Page_Up:
                self.wNotebook.prev_page()
                return True
            elif event.keyval == gtk.keysyms.Page_Down:
                self.wNotebook.next_page()
                return True

        # After this code we only handle Maemo hardware keys,
        # so if we are not a Maemo app, we don't do anything
        if not gpodder.ui.maemo:
            return False
        
        diff = 0
        if event.keyval == gtk.keysyms.F7: #plus
            diff = 1
        elif event.keyval == gtk.keysyms.F8: #minus
            diff = -1

        if diff != 0 and not self.currently_updating:
            selection = self.treeChannels.get_selection()
            (model, iter) = selection.get_selected()
            new_path = ((model.get_path(iter)[0]+diff)%len(model),)
            selection.select_path(new_path)
            self.treeChannels.set_cursor(new_path)
            return True

        return False

    def on_iconify(self):
        if self.tray_icon:
            self.gPodder.set_skip_taskbar_hint(False)
        else:
            self.gPodder.set_skip_taskbar_hint(False)

    def on_uniconify(self):
        if self.tray_icon:
            self.gPodder.set_skip_taskbar_hint(False)
        else:
            self.gPodder.set_skip_taskbar_hint(False)

    def uniconify_main_window(self):
        # We need to hide and then show the window in WMs like Metacity
        # or KWin4 to move the window to the active workspace
        # (see http://gpodder.org/bug/1125)
        self.gPodder.hide()
        self.gPodder.show()
        self.gPodder.present()
 
    def iconify_main_window(self):
        if not self.is_iconified():
            self.gPodder.hide()

    def update_podcasts_tab(self):
        if gpodder.ui.fremantle:
            return

        self.label2.set_text(_('Podcasts'))
        count = len(self.channels)
        if count:
            self.label2.set_text(self.label2.get_text() + ' (%d)' % count)

    @dbus.service.method(gpodder.dbus_interface)
    def show_gui_window(self):
        parent = self.get_dialog_parent()
        parent.present()

    @dbus.service.method(gpodder.dbus_interface)
    def subscribe_to_url(self, url):
        gPodderAddPodcast(self.gPodder,
                add_urls_callback=self.add_podcast_list,
                preset_url=url)

    @dbus.service.method(gpodder.dbus_interface)
    def mark_episode_played(self, filename):
        if filename is None:
            return False

        for channel in self.channels:
            for episode in channel.get_all_episodes():
                fn = episode.local_filename(create=False, check_only=True)
                if fn == filename:
                    episode.mark(is_played=True)
                    self.db.commit()
                    self.update_episode_list_icons([episode.url])
                    self.update_podcast_list_model([episode.channel.url])
                    return True

        return False


def main(options=None):
    gobject.threads_init()
    gobject.set_application_name('gPodder')

    if gpodder.ui.maemo:
        # Try to enable the custom icon theme for gPodder on Maemo
        settings = gtk.settings_get_default()
        settings.set_string_property('gtk-icon-theme-name', \
                                     'gpodder', __file__)
        # Extend the search path for the optified icon theme (Maemo 5)
        icon_theme = gtk.icon_theme_get_default()
        icon_theme.prepend_search_path('/opt/gpodder-icon-theme/')

        # Add custom icons for the new Maemo 5 look :)
        for id in ('audio', 'video', 'download', 'audio-locked', 'video-locked'):
            filename = os.path.join(gpodder.images_folder, '%s.png' % id)
            pixbuf = gtk.gdk.pixbuf_new_from_file(filename)
            gtk.icon_theme_add_builtin_icon('gpodder-%s' % id, 40, pixbuf)

    gtk.window_set_default_icon_name('gpodder')
    gtk.about_dialog_set_url_hook(lambda dlg, link, data: util.open_website(link), None)

    try:
        dbus_main_loop = dbus.glib.DBusGMainLoop(set_as_default=True)
        gpodder.dbus_session_bus = dbus.SessionBus(dbus_main_loop)

        bus_name = dbus.service.BusName(gpodder.dbus_bus_name, bus=gpodder.dbus_session_bus)
    except dbus.exceptions.DBusException, dbe:
        log('Warning: Cannot get "on the bus".', traceback=True)
        dlg = gtk.MessageDialog(None, gtk.DIALOG_MODAL, gtk.MESSAGE_ERROR, \
                gtk.BUTTONS_CLOSE, _('Cannot start gPodder'))
        dlg.format_secondary_markup(_('D-Bus error: %s') % (str(dbe),))
        dlg.set_title('gPodder')
        dlg.run()
        dlg.destroy()
        sys.exit(0)

    util.make_directory(gpodder.home)
    gpodder.load_plugins()

    config = UIConfig(gpodder.config_file)

    # Load hook modules and install the hook manager globally
    # if modules have been found an instantiated by the manager
    user_hooks = hooks.HookManager()
    if user_hooks.has_modules():
        gpodder.user_hooks = user_hooks

    if gpodder.ui.diablo:
        # Detect changing of SD cards between mmc1/mmc2 if a gpodder
        # folder exists there (allow moving "gpodder" between SD cards or USB)
        # Also allow moving "gpodder" to home folder (e.g. rootfs on SD)
        if not os.path.exists(config.download_dir):
            log('Downloads might have been moved. Trying to locate them...')
            for basedir in ['/media/mmc1', '/media/mmc2']+glob.glob('/media/usb/*')+['/home/user/MyDocs']:
                dir = os.path.join(basedir, 'gpodder')
                if os.path.exists(dir):
                    log('Downloads found in: %s', dir)
                    config.download_dir = dir
                    break
                else:
                    log('Downloads NOT FOUND in %s', dir)

    if config.enable_fingerscroll:
        BuilderWidget.use_fingerscroll = True

    config.mygpo_device_type = util.detect_device_type()

    gp = gPodder(bus_name, config)

    # Handle options
    if options.subscribe:
        util.idle_add(gp.subscribe_to_url, options.subscribe)

    # mac OS X stuff :
    # handle "subscribe to podcast" events from firefox
    if platform.system() == 'Darwin':
        from gpodder import gpodderosx
        gpodderosx.register_handlers(gp)
    # end mac OS X stuff

    gp.run()



