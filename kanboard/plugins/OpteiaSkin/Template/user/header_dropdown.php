<li>
    <?= $this->url->icon(
        'adjust', 
        t('Theme Mode'), 
        'UserSettingsController', 
        'show', 
        array('plugin' => 'OpteiaSkin', 'user_id' => $this->user->getId())
    ) ?>
</li>
