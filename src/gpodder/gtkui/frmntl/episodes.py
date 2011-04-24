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

import gtk
import hildon

import gpodder

_ = gpodder.gettext

from gpodder import util

from gpodder.gtkui.interface.common import BuilderWidget
from gpodder.gtkui.model import EpisodeListModel
from gpodder.gtkui.model import PodcastChannelProxy

from gpodder.gtkui.frmntl.episodeactions import gPodderEpisodeActions

class gPodderEpisodes(BuilderWidget):
    def new(self):
        self.channel = None

        # Styling for the label that appears when the list is empty
        hildon.hildon_helper_set_logical_font(self.empty_label, \
                'LargeSystemFont')
        hildon.hildon_helper_set_logical_color(self.empty_label, \
                gtk.RC_FG, gtk.STATE_NORMAL, 'SecondaryTextColor')

        self.episode_actions = gPodderEpisodeActions(self.main_window, \
                episode_list_status_changed=self.episode_list_status_changed, \
                episode_is_downloading=self.episode_is_downloading, \
                show_episode_shownotes=self.show_episode_shownotes, \
                playback_episodes=self.playback_episodes, \
                download_episode_list=self.download_episode_list, \
                show_episode_in_download_manager=self.show_episode_in_download_manager, \
                add_download_task_monitor=self.add_download_task_monitor, \
                remove_download_task_monitor=self.remove_download_task_monitor, \
                for_each_episode_set_task_status=self.for_each_episode_set_task_status, \
                delete_episode_list=self.delete_episode_list)

        # Tap-and-hold (aka "long press") context menu
        self.touched_episode = None
        self.context_menu = gtk.Menu()
        # "Emulate" hildon_gtk_menu_new
        self.context_menu.set_name('hildon-context-sensitive-menu')
        self.context_menu.append(self.action_shownotes.create_menu_item())
        self.context_menu.append(self.action_download.create_menu_item())
        self.context_menu.append(self.action_delete.create_menu_item())
        self.context_menu.append(gtk.SeparatorMenuItem())
        self.context_menu.append(self.action_keep.create_menu_item())
        self.context_menu.append(self.action_mark_as_old.create_menu_item())
        self.context_menu.show_all()
        self.treeview.tap_and_hold_setup(self.context_menu)

        # Workaround for Maemo bug XXX
        self.button_search_episodes_clear.set_name('HildonButton-thumb')
        appmenu = hildon.AppMenu()
        for action in (self.action_update, \
                       self.action_rename, \
                       self.action_login, \
                       self.action_website, \
                       self.action_unsubscribe, \
                       self.action_check_for_new_episodes, \
                       self.action_delete_episodes):
            button = gtk.Button()
            action.connect_proxy(button)
            appmenu.append(button)

        self.pause_sub_button = hildon.CheckButton(gtk.HILDON_SIZE_FINGER_HEIGHT)
        self.pause_sub_button.set_label(_('Pause subscription'))
        self.pause_sub_button.connect('toggled', self.on_pause_subscription_button_toggled)
        appmenu.append(self.pause_sub_button)

        self.keep_episodes_button = hildon.CheckButton(gtk.HILDON_SIZE_FINGER_HEIGHT)
        self.keep_episodes_button.set_label(_('Keep episodes'))
        self.keep_episodes_button.connect('toggled', self.on_keep_episodes_button_toggled)
        appmenu.append(self.keep_episodes_button)

        for filter in (self.item_view_episodes_all, \
                       self.item_view_episodes_undeleted, \
                       self.item_view_episodes_downloaded):
            button = gtk.ToggleButton()
            filter.connect_proxy(button)
            appmenu.add_filter(button)
        appmenu.show_all()
        self.main_window.set_app_menu(appmenu)

    def on_pause_subscription_button_toggled(self, widget):
        new_value = not widget.get_active()
        if new_value != self.channel.feed_update_enabled:
            self.channel.feed_update_enabled = new_value
            self.cover_downloader.reload_cover_from_disk(self.channel)
            self.channel.save()
            self.update_podcast_list_model(urls=[self.channel.url])

    def on_keep_episodes_button_toggled(self, widget):
        new_value = widget.get_active()
        if new_value != self.channel.channel_is_locked:
            self.channel.channel_is_locked = new_value
            self.channel.update_channel_lock()

        for episode in self.channel.get_all_episodes():
            episode.mark(is_locked=self.channel.channel_is_locked)

        self.update_podcast_list_model(urls=[self.channel.url])
        self.episode_list_status_changed(self.channel.get_all_episodes())

    def on_rename_button_clicked(self, widget):
        if self.channel is None:
            return

        new_title = self.show_text_edit_dialog(_('Rename podcast'), \
                _('New name:'), self.channel.title, \
                affirmative_text=_('Rename'))
        if new_title is not None and new_title != self.channel.title:
            self.channel.set_custom_title(new_title)
            self.main_window.set_title(self.channel.title)
            self.channel.save()
            self.show_message(_('Podcast renamed: %s') % new_title)
            self.update_podcast_list_model(urls=[self.channel.url])

    def on_login_button_clicked(self, widget):
        accept, auth_data = self.show_login_dialog(_('Login to %s') % \
                                                   self.channel.title, '', \
                                                   self.channel.username, \
                                                   self.channel.password)
        if accept:
            self.channel.username, self.channel.password = auth_data
            self.channel.save()

    def on_website_button_clicked(self, widget):
        if self.channel is not None:
            util.open_website(self.channel.link)

    def on_update_button_clicked(self, widget):
        self.on_itemUpdateChannel_activate()

    def on_unsubscribe_button_clicked(self, widget):
        self.on_delete_event(widget, None)
        self.on_itemRemoveChannel_activate(widget)

    def on_episode_selected(self, treeview, path, column):
        model = treeview.get_model()
        episode = model.get_value(model.get_iter(path), \
                EpisodeListModel.C_EPISODE)
        self.episode_actions.show_episode(episode)

    def on_delete_event(self, widget, event):
        self.main_window.hide()
        self.channel = None
        self.hide_episode_search()
        return True

    def on_treeview_button_press(self, widget, event):
        result = self.treeview.get_path_at_pos(int(event.x), int(event.y))
        if result is not None:
            path, column, x, y = result
            model = self.treeview.get_model()
            episode = model.get_value(model.get_iter(path), \
                    EpisodeListModel.C_EPISODE)

            self.action_delete.set_property('visible', not episode.is_locked)

            if episode.was_downloaded():
                self.action_keep.set_property('visible', True)
                self.action_download.set_property('visible', not episode.was_downloaded(and_exists=True))
            else:
                self.action_keep.set_property('visible', False)
                self.action_download.set_property('visible', not self.episode_is_downloading(episode))

            self.touched_episode = None

            self.action_keep.set_active(episode.is_locked)
            self.action_mark_as_old.set_active(not episode.is_played)

            self.touched_episode = episode
        else:
            self.touched_episode = None

    def on_shownotes_button_clicked(self, widget):
        if self.touched_episode is not None:
            self.show_episode_shownotes(self.touched_episode)

    def on_download_button_clicked(self, widget):
        if self.touched_episode is not None:
            self.show_message(_('Downloading episode'))
            self.download_episode_list([self.touched_episode])

    def on_delete_button_clicked(self, widget):
        if self.touched_episode is not None:
            self.delete_episode_list([self.touched_episode])

    def on_keep_button_clicked(self, widget):
        if self.touched_episode is not None:
            self.touched_episode.mark(is_locked=not self.touched_episode.is_locked)
            self.episode_list_status_changed([self.touched_episode])

    def on_mark_as_old_button_clicked(self, widget):
        if self.touched_episode is not None:
            self.touched_episode.mark(is_played=not self.touched_episode.is_played)
            self.episode_list_status_changed([self.touched_episode])

    def on_check_for_new_episodes_button_clicked(self, widget):
        self.show_message(_('Checking for new episodes...'))
        self.on_itemUpdate_activate(widget)

    def on_delete_episodes_button_clicked(self, widget):
        all_episodes = isinstance(self.channel, PodcastChannelProxy)
        if all_episodes:
            self.show_delete_episodes_window()
        else:
            self.show_delete_episodes_window(self.channel)

    def show(self):
        # Check if we are displaying the "all episodes" view
        all_episodes = isinstance(self.channel, PodcastChannelProxy)

        for action in (self.action_rename, \
                       self.action_login, \
                       self.action_unsubscribe, \
                       self.action_update):
            action.set_visible(not all_episodes)

        self.action_check_for_new_episodes.set_visible(all_episodes)
        self.action_delete_episodes.set_visible(True)
        self.action_website.set_visible(True)

        if all_episodes:
            self.pause_sub_button.hide()
            self.keep_episodes_button.hide()
        else:
            self.pause_sub_button.show()
            self.keep_episodes_button.show()

        self.pause_sub_button.set_active(\
                not self.channel.feed_update_enabled)
        self.keep_episodes_button.set_active(\
                self.channel.channel_is_locked)

        self.main_window.set_title(self.channel.title)
        self.main_window.show()
        self.treeview.grab_focus()

