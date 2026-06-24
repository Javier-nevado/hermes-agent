<?php if ($this->user->isCurrentUser($user['id'])): ?>
<li <?= $this->app->checkMenuSelection('UserSettingsController', 'show', 'OpteiaSkin') ?>>
    <?= $this->url->link(t('Theme Mode'), 'UserSettingsController', 'show', array('plugin' => 'OpteiaSkin', 'user_id' => $user['id'])) ?>
</li>
<?php endif ?>
