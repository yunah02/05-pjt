from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comments', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='commentanalysis',
            name='final_comments',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='commentanalysis',
            name='logs',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
