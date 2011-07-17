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

# gpodder.gtkui.desktop.sync - Glue code between GTK+ UI and sync module
# Thomas Perl <thp@gpodder.org>; 2009-09-05 (based on code from gui.py)

import gtk
import threading

import gpodder

_ = gpodder.gettext

from gpodder import util
from gpodder import sync
from gpodder.liblogger import log

from gpodder.gtkui.desktop.syncprogress import gPodderSyncProgress
from gpodder.gtkui.desktop.deviceplaylist import gPodderDevicePlaylist

class gPodderSyncUI(object):
    def __init__(self, config, notification, \
            parent_window, show_confirmation, \
            update_episode_list_icons, \
            update_podcast_list_model, \
            preferences_widget, \
            episode_selector_class, \
            commit_changes_to_database):
        self._config = config
        self.notification = notification
        self.parent_window = parent_window
        self.show_confirmation = show_confirmation
        self.update_episode_list_icons = update_episode_list_icons
        self.update_podcast_list_model = update_podcast_list_model
        self.preferences_widget = preferences_widget
        self.episode_selector_class = episode_selector_class
        self.commit_changes_to_database = commit_changes_to_database

    def _filter_sync_episodes(self, channels, only_downloaded=True):
        """Return a list of episodes for device synchronization

        If only_downloaded is True, this will skip episodes that
        have not been downloaded yet and podcasts that are marked
        as "Do not synchronize to my device".
        """
        episodes = []
        for channel in channels:
            if not channel.sync_to_devices and only_downloaded:
                log('Skipping channel: %s', channel.title, sender=self)
                continue

            for episode in channel.get_all_episodes():
                if episode.was_downloaded(and_exists=True) or \
                        not only_downloaded:
                    episodes.append(episode)
        return episodes

    def _show_message_unconfigured(self):
        title = _('No device configured')
        message = _('Please set up your device in the preferences dialog.')
        self.notification(message, title, widget=self.preferences_widget)

    def _show_message_cannot_open(self):
        title = _('Cannot open device')
        message = _('Please check the settings in the preferences dialog.')
        self.notification(message, title, widget=self.preferences_widget)

    def on_synchronize_episodes(self, channels, episodes=None, force_played=True):
        if self._config.device_type == 'ipod' and not sync.gpod_available:
            title = _('Cannot sync to iPod')
            message = _('Please install python-gpod and restart gPodder.')
            self.notification(message, title, important=True)
            return
        elif self._config.device_type == 'mtp' and not sync.pymtp_available:
            title = _('Cannot sync to MTP device')
            message = _('Please install libmtp and restart gPodder.')
            self.notification(message, title, important=True)
            return

        device = sync.open_device(self._config)
        if device is not None:
            def after_device_sync_callback(device, successful_sync):
                if device.cancelled:
                    log('Cancelled by user.', sender=self)
                elif successful_sync:
                    title = _('Device synchronized')
                    message = _('Your device has been synchronized.')
                    self.notification(message, title)
                else:
                    title = _('Error closing device')
                    message = _('Please check settings and permission.')
                    self.notification(message, title, important=True)

                # Update the UI to reflect changes from the sync process
                episode_urls = set()
                channel_urls = set()
                for episode in episodes:
                    episode_urls.add(episode.url)
                    channel_urls.add(episode.channel.url)
                util.idle_add(self.update_episode_list_icons, episode_urls)
                util.idle_add(self.update_podcast_list_model, channel_urls)
                util.idle_add(self.commit_changes_to_database)
            device.register('post-done', after_device_sync_callback)

        if device is None:
            return self._show_message_unconfigured()

        if not device.open():
            return self._show_message_cannot_open()

        if self._config.device_type == 'ipod':
            #update played episodes and delete if requested
            for channel in channels:
                if channel.sync_to_devices:
                    allepisodes = [e for e in channel.get_all_episodes() \
                            if e.was_downloaded(and_exists=True)]
                    device.update_played_or_delete(channel, allepisodes, \
                            self._config.ipod_delete_played_from_db)

            if self._config.ipod_purge_old_episodes:
                device.purge()

        if episodes is None:
            force_played = False
            episodes = self._filter_sync_episodes(channels)

        def check_free_space():
            # "Will we add this episode to the device?"
            def will_add(episode):
                # If already on-device, it won't take up any space
                if device.episode_on_device(episode):
                    return False

                # Might not be synced if it's played already
                if not force_played and \
                        self._config.only_sync_not_played and \
                        episode.is_played:
                    return False

                # In all other cases, we expect the episode to be
                # synchronized to the device, so "answer" positive
                return True

            # "What is the file size of this episode?"
            def file_size(episode):
                filename = episode.local_filename(create=False)
                if filename is None:
                    return 0
                return util.calculate_size(str(filename))

            # Calculate total size of sync and free space on device
            total_size = sum(file_size(e) for e in episodes if will_add(e))
            free_space = max(device.get_free_space(), 0)

            if total_size > free_space:
                title = _('Not enough space left on device')
                message = _('You need to free up %s.\nDo you want to continue?') \
                                % (util.format_filesize(total_size-free_space),)
                if not self.show_confirmation(message, title):
                    device.cancel()
                    device.close()
                    return

            # Finally start the synchronization process
            gPodderSyncProgress(self.parent_window, device=device)
            def sync_thread_func():
                device.add_tracks(episodes, force_played=force_played)
                device.close()
            threading.Thread(target=sync_thread_func).start()

        # This function is used to remove files from the device
        def cleanup_episodes():
            # 'only_sync_not_played' must be used or else all the
            # played tracks will be copied then immediately deleted
            if self._config.mp3_player_delete_played and \
                    self._config.only_sync_not_played:
                all_episodes = self._filter_sync_episodes(channels, \
                        only_downloaded=False)
                episodes_on_device = device.get_all_tracks()
                for local_episode in all_episodes:
                    episode = device.episode_on_device(local_episode)
                    if episode is None:
                        continue

                    if local_episode.state == gpodder.STATE_DELETED \
                            or (local_episode.is_played and \
                                not local_episode.is_locked):
                        log('Removing episode from device: %s',
                                episode.title, sender=self)
                        device.remove_track(episode)

            # When this is done, start the callback in the UI code
            util.idle_add(check_free_space)

        # This will run the following chain of actions:
        #  1. Remove old episodes (in worker thread)
        #  2. Check for free space (in UI thread)
        #  3. Sync the device (in UI thread)
        threading.Thread(target=cleanup_episodes).start()

    def on_cleanup_device(self):
        columns = (
                ('title', None, None, _('Episode')),
                ('podcast', None, None, _('Podcast')),
                ('filesize', 'length', int, _('Size')),
                ('modified', 'modified_sort', int, _('Copied')),
                ('playcount_str', 'playcount', int, _('Play count')),
                ('released', None, None, _('Released')),
        )

        device = sync.open_device(self._config)

        if device is None:
            return self._show_message_unconfigured()

        if not device.open():
            return self._show_message_cannot_open()

        tracks = device.get_all_tracks()
        if tracks:
            def remove_tracks_callback(tracks):
                title = _('Delete podcasts from device?')
                message = _('Do you really want to remove these episodes from your device? Episodes in your library will not be deleted.')
                if tracks and self.show_confirmation(message, title):
                    gPodderSyncProgress(self.parent_window, device=device)
                    def cleanup_thread_func():
                        device.remove_tracks(tracks)
                        if not device.close():
                            title = _('Error closing device')
                            message = _('There has been an error closing your device.')
                            self.notification(message, title, important=True)
                    threading.Thread(target=cleanup_thread_func).start()

            wanted_columns = []
            for key, sort_name, sort_type, caption in columns:
                want_this_column = False
                for track in tracks:
                    if getattr(track, key) is not None:
                        want_this_column = True
                        break

                if want_this_column:
                    wanted_columns.append((key, sort_name, sort_type, caption))
            title = _('Remove podcasts from device')
            instructions = _('Select episodes to remove from your device.')
            self.episode_selector_class(self.parent_window, title=title, \
                                        instructions=instructions, \
                                        episodes=tracks, columns=wanted_columns, \
                                        stock_ok_button=gtk.STOCK_DELETE, \
                                        callback=remove_tracks_callback, \
                                        tooltip_attribute=None, \
                                        _config=self._config)
        else:
            device.close()
            title = _('No files on device')
            message = _('The devices contains no files to be removed.')
            self.notification(message, title)

    def on_manage_device_playlist(self):
        if self._config.device_type == 'ipod' and not sync.gpod_available:
            title = _('Cannot manage iPod playlist')
            message = _('This feature is not available for iPods.')
            self.notification(message, title)
            return
        elif self._config.device_type == 'mtp' and not sync.pymtp_available:
            title = _('Cannot manage MTP device playlist')
            message = _('This feature is not available for MTP devices.')
            self.notification(message, title)
            return

        device = sync.open_device(self._config)

        if device is None:
            return self._show_message_unconfigured()

        if not device.open():
            return self._show_message_cannot_open()

        gPodderDevicePlaylist(self.parent_window, \
                              device=device, \
                              _config=self._config)
        device.close()

